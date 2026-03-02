"""
GMX Market Depth and Price Impact Analysis Module.

This module provides tools to inspect GMX v2 market depth and estimate price
impact before opening a position, allowing traders to optimise position sizing.

**Key concepts:**

GMX v2 is not an order book -- "market depth" means:

1. How much open interest (OI) can still be added before the pool reserve cap
   is hit (``available_long_oi_usd`` / ``available_short_oi_usd``).

2. What price impact a given position size will incur, based on the OI imbalance
   between longs and shorts.

The price impact formula is implemented in two Solidity contracts in the
`gmx-synthetics <https://github.com/gmx-io/gmx-synthetics>`_ repository:

- `PricingUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/pricing/PricingUtils.sol>`_
  — defines ``applyImpactFactor``, ``getPriceImpactUsdForSameSideRebalance``, and
  ``getPriceImpactUsdForCrossoverRebalance``.
- `PositionPricingUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/pricing/PositionPricingUtils.sol>`_
  — calls the above helpers from ``getPriceImpactUsd``.

The formula (from ``PricingUtils.applyImpactFactor``):

.. code-block:: text

    price_impact = applyImpactFactor(initialDiff) - applyImpactFactor(nextDiff)
    applyImpactFactor(diffUsd) = diffUsd ^ exponent * factor

where ``initialDiff = |longOI - shortOI|`` and ``nextDiff = |nextLongOI - nextShortOI|``.

A **negative** impact is a cost to the trader (worsens execution price).
A **positive** impact is a rebate (improves execution price -- only when the
trade reduces the existing imbalance).

.. note::

    Virtual inventory (cross-market impact) used by ``PositionPricingUtils.getPriceImpactUsd``
    is intentionally omitted here for simplicity, matching the existing
    ``_estimate_price_impact`` helper in :mod:`eth_defi.gmx.order.base_order`.

Example usage:

.. code-block:: python

    from eth_defi.gmx.api import GMXAPI
    from eth_defi.gmx.market_depth import (
        estimate_position_price_impact,
        fetch_price_impact_params,
        find_max_position_size,
    )

    # Get market depth info (REST-based, cached 60 s, no RPC cost)
    api = GMXAPI(chain="arbitrum")
    eth_markets = api.get_market_depth(market_symbol="ETH")
    eth = eth_markets[0]

    print(f"Long OI: ${eth.long_open_interest_usd:,.0f}")
    print(f"Available long capacity: ${eth.available_long_oi_usd:,.0f}")

    # Fetch price impact params from DataStore (on-chain, cached implicitly)
    config = GMXConfig(web3)
    params = fetch_price_impact_params(config, eth.market_token_address)

    # Estimate impact for a $10 000 long position
    impact_usd = estimate_position_price_impact(
        long_open_interest_usd=eth.long_open_interest_usd,
        short_open_interest_usd=eth.short_open_interest_usd,
        size_delta_usd=10_000.0,
        is_long=True,
        params=params,
    )
    impact_bps = abs(impact_usd) / 10_000.0 * 10_000  # basis points
    print(f"Price impact for $10k long: ${impact_usd:+.2f} ({impact_bps:.2f} bps)")

    # Find the largest position that keeps impact under 5 bps
    max_size = find_max_position_size(
        long_open_interest_usd=eth.long_open_interest_usd,
        short_open_interest_usd=eth.short_open_interest_usd,
        is_long=True,
        max_price_impact_bps=5.0,
        params=params,
        max_oi_available_usd=eth.available_long_oi_usd,
    )
    print(f"Max position size at ≤5 bps impact: ${max_size:,.0f}")
"""

import logging
from dataclasses import dataclass
from typing import Any

from eth_typing import HexAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_DEFAULT_SEARCH_MAX_USD, PRECISION
from eth_defi.gmx.keys import (
    max_position_impact_factor_key,
    position_impact_exponent_factor_key,
    position_impact_factor_key,
)
from eth_defi.gmx.types import USDAmount

logger = logging.getLogger(__name__)

#: GMX fixed-point scalar: all OI, liquidity, and rate values from the REST API
#: and DataStore are integers; divide by this to get plain USD / dimensionless floats.
#: Derived from :data:`eth_defi.gmx.constants.PRECISION` (= 30).
_PRECISION = 10**PRECISION


@dataclass(slots=True)
class MarketDepthInfo:
    """Market depth snapshot for a single GMX v2 perpetual market.

    Populated from the ``/markets/info`` REST endpoint (cached 60 s).
    All USD values are already divided by ``10^30`` (the GMX fixed-point
    precision) and expressed as plain floats.

    Pool amounts (``long_pool_amount``, ``short_pool_amount``) are kept in
    raw token units as returned by the API, because the conversion to USD
    requires per-token decimal information.
    """

    #: GMX market token address (identifies the GM liquidity pool)
    market_token_address: HexAddress
    #: Human-readable market name, e.g. ``"ETH/USD [WETH-USDC]"``
    market_symbol: str
    #: Address of the index token (the asset being traded)
    index_token_address: HexAddress
    #: Address of the long collateral token
    long_token_address: HexAddress
    #: Address of the short collateral token (usually USDC)
    short_token_address: HexAddress
    #: Current long open interest in USD
    long_open_interest_usd: USDAmount
    #: Current short open interest in USD
    short_open_interest_usd: USDAmount
    #: Maximum long OI the pool can accept before hitting the reserve cap (USD).
    #: Computed as ``long_open_interest_usd + available_long_oi_usd``.
    max_long_open_interest_usd: USDAmount
    #: Maximum short OI the pool can accept before hitting the reserve cap (USD).
    #: Computed as ``short_open_interest_usd + available_short_oi_usd``.
    max_short_open_interest_usd: USDAmount
    #: Remaining long OI capacity in USD.
    #: This is the USD amount of new long positions the pool can absorb.
    available_long_oi_usd: USDAmount
    #: Remaining short OI capacity in USD.
    #: This is the USD amount of new short positions the pool can absorb.
    available_short_oi_usd: USDAmount
    #: Long pool token amount (raw token units as returned by the API)
    long_pool_amount: float
    #: Short pool token amount (raw token units as returned by the API)
    short_pool_amount: float
    #: Long funding rate (30-decimal fixed point divided, dimensionless per time unit as reported by GMX)
    long_funding_rate: float
    #: Short funding rate (30-decimal fixed point divided, dimensionless per time unit as reported by GMX)
    short_funding_rate: float
    #: Long borrowing rate (30-decimal fixed point divided, dimensionless per time unit as reported by GMX)
    long_borrowing_rate: float
    #: Short borrowing rate (30-decimal fixed point divided, dimensionless per time unit as reported by GMX)
    short_borrowing_rate: float
    #: Whether this market is currently listed for trading
    is_listed: bool


@dataclass(slots=True)
class PriceImpactParams:
    """Per-market price impact parameters read from the GMX DataStore contract.

    All factor and exponent values use GMX's 30-decimal fixed-point format
    (integers). Pass them directly to :func:`estimate_position_price_impact`.
    """

    #: Impact factor for positive (balance-improving) trades (30-decimal int)
    positive_factor: int
    #: Impact factor for negative (balance-worsening) trades (30-decimal int)
    negative_factor: int
    #: Exponent factor for positive impact (30-decimal int)
    positive_exponent: int
    #: Exponent factor for negative impact (30-decimal int)
    negative_exponent: int
    #: Maximum position impact factor cap for positive impact (30-decimal int).
    #: ``0`` means no explicit cap applies.
    max_positive_factor: int
    #: Maximum position impact factor cap for negative impact (30-decimal int).
    #: ``0`` means no explicit cap applies.
    max_negative_factor: int


def parse_market_depth(market_data: dict[str, Any]) -> MarketDepthInfo:
    """Parse a single market entry from the ``/markets/info`` REST response.

    :param market_data:
        Raw market dictionary as returned inside ``response["markets"]``.
    :return:
        Structured :class:`MarketDepthInfo` for this market.
    """
    oi_long_raw = int(market_data.get("openInterestLong", 0))
    oi_short_raw = int(market_data.get("openInterestShort", 0))
    avail_long_raw = int(market_data.get("availableLiquidityLong", 0))
    avail_short_raw = int(market_data.get("availableLiquidityShort", 0))

    long_oi_usd = oi_long_raw / _PRECISION
    short_oi_usd = oi_short_raw / _PRECISION
    avail_long_usd = avail_long_raw / _PRECISION
    avail_short_usd = avail_short_raw / _PRECISION

    # Clamp available liquidity to zero (can be marginally negative due to rounding)
    avail_long_usd = max(0.0, avail_long_usd)
    avail_short_usd = max(0.0, avail_short_usd)

    funding_long_raw = market_data.get("fundingRateLong", 0)
    funding_short_raw = market_data.get("fundingRateShort", 0)
    borrow_long_raw = market_data.get("borrowingRateLong", 0)
    borrow_short_raw = market_data.get("borrowingRateShort", 0)

    return MarketDepthInfo(
        market_token_address=market_data.get("marketToken", ""),
        market_symbol=market_data.get("name", ""),
        index_token_address=market_data.get("indexToken", ""),
        long_token_address=market_data.get("longToken", ""),
        short_token_address=market_data.get("shortToken", ""),
        long_open_interest_usd=long_oi_usd,
        short_open_interest_usd=short_oi_usd,
        max_long_open_interest_usd=long_oi_usd + avail_long_usd,
        max_short_open_interest_usd=short_oi_usd + avail_short_usd,
        available_long_oi_usd=avail_long_usd,
        available_short_oi_usd=avail_short_usd,
        long_pool_amount=float(market_data.get("poolAmountLong", 0)),
        short_pool_amount=float(market_data.get("poolAmountShort", 0)),
        long_funding_rate=int(funding_long_raw) / _PRECISION,
        short_funding_rate=int(funding_short_raw) / _PRECISION,
        long_borrowing_rate=int(borrow_long_raw) / _PRECISION,
        short_borrowing_rate=int(borrow_short_raw) / _PRECISION,
        is_listed=bool(market_data.get("isListed", False)),
    )


def _apply_impact_factor(diff_usd: float, factor: int, exponent: int) -> float:
    """Apply the GMX price impact factor formula to an OI imbalance.

    Mirrors the Solidity ``applyImpactFactor`` helper in
    `PricingUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/pricing/PricingUtils.sol>`_.

    :param diff_usd: OI imbalance in USD (non-negative)
    :param factor: 30-decimal fixed-point impact factor integer
    :param exponent: 30-decimal fixed-point exponent integer
    :return: Impact contribution in USD
    """
    if diff_usd <= 0.0:
        return 0.0
    exponent_float = exponent / _PRECISION
    factor_float = factor / _PRECISION
    return (diff_usd**exponent_float) * factor_float


def estimate_position_price_impact(
    long_open_interest_usd: USDAmount,
    short_open_interest_usd: USDAmount,
    size_delta_usd: USDAmount,
    is_long: bool,
    params: PriceImpactParams,
) -> float:
    """Estimate price impact for a position using the GMX v2 formula.

    This is a pure-Python implementation -- no RPC calls are needed.
    Pass ``PriceImpactParams`` obtained via :func:`fetch_price_impact_params`.

    The GMX price impact formula from
    `PricingUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/pricing/PricingUtils.sol>`_
    (called by
    `PositionPricingUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/pricing/PositionPricingUtils.sol>`_):

    .. code-block:: text

        applyImpactFactor(diffUsd) = diffUsd ^ exponent * factor
        impact = applyImpactFactor(initialDiff) - applyImpactFactor(nextDiff)

    where ``initialDiff`` and ``nextDiff`` are the absolute OI imbalances
    before and after the trade respectively.

    .. note::

        Virtual inventory (cross-market impact) is not modelled here, matching
        the behaviour of the existing ``_estimate_price_impact`` helper in
        :mod:`eth_defi.gmx.order.base_order`.

    :param long_open_interest_usd: Current long open interest in USD
    :param short_open_interest_usd: Current short open interest in USD
    :param size_delta_usd: Position size to open in USD (must be positive)
    :param is_long: ``True`` for a long position, ``False`` for short
    :param params: Price impact parameters fetched from the DataStore contract
    :return:
        Price impact in USD. Negative values are a cost to the trader
        (execution price worsens). Positive values are a rebate.
    """
    if size_delta_usd <= 0.0:
        return 0.0

    initial_long = long_open_interest_usd
    initial_short = short_open_interest_usd
    initial_diff = abs(initial_long - initial_short)

    if is_long:
        next_long = initial_long + size_delta_usd
        next_short = initial_short
    else:
        next_long = initial_long
        next_short = initial_short + size_delta_usd

    next_diff = abs(next_long - next_short)

    balance_improved = next_diff < initial_diff

    # Detect crossover: the imbalance has flipped sides
    initially_long_skewed = initial_long >= initial_short
    next_long_skewed = next_long >= next_short
    crossover = initially_long_skewed != next_long_skewed

    if crossover:
        # Split into two parts:
        # 1) the portion that reduces the old imbalance to zero (positive impact)
        # 2) the portion that creates new imbalance on the opposite side (negative impact)
        positive_impact = _apply_impact_factor(initial_diff, params.positive_factor, params.positive_exponent)
        negative_impact = _apply_impact_factor(next_diff, params.negative_factor, params.negative_exponent)
        price_impact = positive_impact - negative_impact
    elif balance_improved:
        # Same-side rebalance, trade reduces imbalance -- positive (rebate)
        initial_impact = _apply_impact_factor(initial_diff, params.positive_factor, params.positive_exponent)
        next_impact = _apply_impact_factor(next_diff, params.positive_factor, params.positive_exponent)
        price_impact = initial_impact - next_impact
    else:
        # Same-side rebalance, trade worsens imbalance -- negative (cost)
        initial_impact = _apply_impact_factor(initial_diff, params.negative_factor, params.negative_exponent)
        next_impact = _apply_impact_factor(next_diff, params.negative_factor, params.negative_exponent)
        price_impact = initial_impact - next_impact

    # Apply max positive impact cap if configured
    if price_impact > 0.0 and params.max_positive_factor > 0:
        max_positive_usd = size_delta_usd * (params.max_positive_factor / _PRECISION)
        price_impact = min(price_impact, max_positive_usd)

    return price_impact


def fetch_price_impact_params(
    config: GMXConfig,
    market_address: HexAddress,
) -> PriceImpactParams:
    """Fetch price impact parameters from the GMX DataStore contract.

    Reads the six per-market parameters needed by
    :func:`estimate_position_price_impact` via individual ``getUint`` calls on
    the DataStore contract.

    :param config: Initialised GMX configuration with web3 connection
    :param market_address: GMX market token address (``MarketDepthInfo.market_token_address``)
    :return: Populated :class:`PriceImpactParams`
    """
    # Local import to avoid circular dependency: contracts.py imports GMXAPI from api.py
    from eth_defi.gmx.contracts import get_datastore_contract  # noqa: PLC0415

    datastore = get_datastore_contract(config.web3, config.chain)

    positive_factor = datastore.functions.getUint(position_impact_factor_key(market_address, True)).call()
    negative_factor = datastore.functions.getUint(position_impact_factor_key(market_address, False)).call()
    positive_exponent = datastore.functions.getUint(position_impact_exponent_factor_key(market_address, True)).call()
    negative_exponent = datastore.functions.getUint(position_impact_exponent_factor_key(market_address, False)).call()
    max_positive_factor = datastore.functions.getUint(max_position_impact_factor_key(market_address, True)).call()
    max_negative_factor = datastore.functions.getUint(max_position_impact_factor_key(market_address, False)).call()

    logger.debug(
        "Fetched price impact params for market %s: positive_factor=%s, negative_factor=%s, positive_exponent=%s, negative_exponent=%s",
        market_address,
        positive_factor,
        negative_factor,
        positive_exponent,
        negative_exponent,
    )

    return PriceImpactParams(
        positive_factor=positive_factor,
        negative_factor=negative_factor,
        positive_exponent=positive_exponent,
        negative_exponent=negative_exponent,
        max_positive_factor=max_positive_factor,
        max_negative_factor=max_negative_factor,
    )


def find_max_position_size(
    long_open_interest_usd: USDAmount,
    short_open_interest_usd: USDAmount,
    is_long: bool,
    max_price_impact_bps: float,
    params: PriceImpactParams,
    max_oi_available_usd: USDAmount = 0.0,
    search_precision_usd: float = 100.0,
) -> USDAmount:
    """Find the maximum position size that keeps price impact within a threshold.

    Uses binary search over the price impact curve, which is monotonically
    increasing with position size for balance-worsening trades.

    :param long_open_interest_usd: Current long open interest in USD
    :param short_open_interest_usd: Current short open interest in USD
    :param is_long: ``True`` for a long position, ``False`` for short
    :param max_price_impact_bps:
        Maximum acceptable price impact expressed in basis points
        (1 bps = 0.01 %). E.g. ``5.0`` means 5 bps = 0.05 %.
    :param params: Price impact parameters from :func:`fetch_price_impact_params`
    :param max_oi_available_usd:
        Upper bound for the search in USD.  If ``0`` (the default), falls back
        to :data:`~eth_defi.gmx.constants.GMX_DEFAULT_SEARCH_MAX_USD` ($100 M),
        which covers typical GMX market sizes.
        Use ``MarketDepthInfo.available_long_oi_usd`` or
        ``MarketDepthInfo.available_short_oi_usd`` here for accuracy.
    :param search_precision_usd:
        The binary search halts when the range is narrower than this value (USD).
        Default ``100.0`` (i.e., $100 precision).
    :return:
        Maximum position size in USD at which the absolute price impact stays
        at or below ``max_price_impact_bps`` basis points.  Returns ``0.0`` if
        even the smallest position exceeds the threshold.
    """
    upper = max_oi_available_usd if max_oi_available_usd > 0.0 else GMX_DEFAULT_SEARCH_MAX_USD
    lower = 0.0

    def impact_bps(size: float) -> float:
        """Compute absolute price impact in bps for *size* USD."""
        if size <= 0.0:
            return 0.0
        impact = estimate_position_price_impact(
            long_open_interest_usd=long_open_interest_usd,
            short_open_interest_usd=short_open_interest_usd,
            size_delta_usd=size,
            is_long=is_long,
            params=params,
        )
        return abs(impact) / size * 10_000.0

    # Check whether any size is feasible
    if impact_bps(upper) <= max_price_impact_bps:
        return upper

    # Binary search
    while upper - lower > search_precision_usd:
        mid = (lower + upper) / 2.0
        if impact_bps(mid) <= max_price_impact_bps:
            lower = mid
        else:
            upper = mid

    return lower
