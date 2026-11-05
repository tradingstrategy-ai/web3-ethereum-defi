"""USDC routing for GMX markets whose only collateral is another stablecoin.

GMX V2 is moving markets to single-token **USDG** pools (USDG is both the long
and the short collateral token).  The first batch (2026-11, Arbitrum) is ZEC,
HYPE, XRP and NEAR; new positions on their old ``[X-USDC]`` pools are blocked
from 2026-11-13.  GMX's guidance for USDC holders: open and close with
``swapPath = [USDC/USDG pool]``, so the order sends USDC, the swap leg turns it
into USDG collateral, and the close swaps the USDG payout back to USDC.

This module holds the routing table and the pure helpers the CCXT adapter and
order classes use:

- :func:`get_usdc_route` — is this collateral token USDC-routable, and through
  which pool;
- :func:`get_usdc_route_for_market_tokens` — is this market a single-token
  routed market (long == short == routed token);
- :func:`assign_usdc_routed_symbols` — give a routed market the plain
  ``X/USDC:USDC`` name when no other market owns it;
- :func:`calculate_swap_min_output` — slippage floor for the increase swap leg;
- :func:`is_zero_open_interest_cap` — the retired-pool signature (both
  ``maxOpenInterest`` caps 0).

The route is explicit rather than discovered: the USDC/USDG pool is a swap-only
market with a zero index token, which
:func:`eth_defi.gmx.utils.determine_swap_route` cannot find, and GMX named the
pool to use.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: GMX USDG token on Arbitrum (6 decimals, GMX ``/tokens``).
USDG_ARBITRUM = "0x004B506865409877C9fA29bfb1ebA929984B9bbC"

#: GMX USDC/USDG swap pool on Arbitrum (swap-only market, zero index token).
USDC_USDG_SWAP_POOL_ARBITRUM = "0x408E8e83d4b8Ac4BDefFDF896B49268A5FE5Ef44"

#: Native USDC on Arbitrum.
USDC_ARBITRUM = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


@dataclass(slots=True, frozen=True)
class UsdcRoute:
    """How to fund and close a position whose collateral is a non-USDC stablecoin.

    The order sends :attr:`pay_token` (USDC), :attr:`swap_path` turns it into
    :attr:`collateral_token` on open, and the same path turns the payout back
    into USDC on close.
    """

    #: Position collateral token (checksummed), e.g. USDG.
    collateral_token: str

    #: Position collateral token symbol, e.g. ``"USDG"``.
    collateral_symbol: str

    #: Token the wallet holds and sends (checksummed), USDC.
    pay_token: str

    #: Symbol of :attr:`pay_token`.
    pay_symbol: str

    #: GMX market addresses for the swap leg, in order (checksummed).
    swap_path: tuple[str, ...] = field(default_factory=tuple)

    def as_info(self) -> dict:
        """Serialise for a CCXT market ``info`` dict (JSON-safe, survives the disk cache).

        :return: Plain dict with string/list values.
        """
        return {
            "collateral_token": self.collateral_token,
            "collateral_symbol": self.collateral_symbol,
            "pay_token": self.pay_token,
            "pay_symbol": self.pay_symbol,
            "swap_path": list(self.swap_path),
        }


#: Collateral tokens that are traded with USDC via a GMX swap leg, per chain.
#:
#: Keyed by lowercase collateral token address.
USDC_ROUTED_COLLATERAL: dict[str, dict[str, UsdcRoute]] = {
    "arbitrum": {
        USDG_ARBITRUM.lower(): UsdcRoute(
            collateral_token=USDG_ARBITRUM,
            collateral_symbol="USDG",
            pay_token=USDC_ARBITRUM,
            pay_symbol="USDC",
            swap_path=(USDC_USDG_SWAP_POOL_ARBITRUM,),
        ),
    },
}


def get_usdc_route(chain: str | None, collateral_token: str | None) -> UsdcRoute | None:
    """Look up the USDC route for a collateral token.

    :param chain: Chain name, e.g. ``"arbitrum"``.
    :param collateral_token: Collateral token address (any case).
    :return: The route, or ``None`` when the token is not USDC-routed on this chain.
    """
    if not chain or not collateral_token:
        return None
    return USDC_ROUTED_COLLATERAL.get(str(chain).lower(), {}).get(collateral_token.lower())


def get_usdc_route_for_market_tokens(chain: str | None, long_token: str | None, short_token: str | None) -> UsdcRoute | None:
    """Return the route when a market's only collateral is a routed token.

    Only single-token markets (long == short) qualify.  A two-token market that
    happens to accept USDG as one side still accepts its other token directly
    and is left alone.

    :param chain: Chain name.
    :param long_token: Market long token address.
    :param short_token: Market short token address.
    :return: The route, or ``None``.
    """
    if not long_token or not short_token or long_token.lower() != short_token.lower():
        return None
    return get_usdc_route(chain, long_token)


def get_usdc_route_from_market(market: dict | None, chain: str | None = None) -> UsdcRoute | None:
    """Return the route recorded on a CCXT market entry.

    Prefers the ``info["usdc_route"]`` tag written by
    :func:`assign_usdc_routed_symbols`; falls back to the market's long/short
    tokens when ``chain`` is given (covers markets loaded by code that did not tag them).

    :param market: CCXT market dict (or ``None``).
    :param chain: Chain name for the token fallback.
    :return: The route, or ``None``.
    """
    if not market:
        return None
    info = market.get("info", market)
    tag = info.get("usdc_route")
    if tag:
        return UsdcRoute(
            collateral_token=tag["collateral_token"],
            collateral_symbol=tag["collateral_symbol"],
            pay_token=tag["pay_token"],
            pay_symbol=tag["pay_symbol"],
            swap_path=tuple(tag["swap_path"]),
        )
    if chain:
        return get_usdc_route_for_market_tokens(
            chain,
            info.get("long_token") or info.get("longToken"),
            info.get("short_token") or info.get("shortToken"),
        )
    return None


def _canonical_base(base: str) -> str:
    """Strip the single-token ``2`` suffix the loaders add (``ZEC2`` -> ``ZEC``)."""
    return base[:-1] if base.endswith("2") and len(base) > 1 else base


def assign_usdc_routed_symbols(
    markets: dict[str, dict],
    chain: str | None,
    excluded_symbols: set[str] | frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """Name USDC-routed markets after their index token when the name is free.

    Loaders name single-token markets ``X2`` (``ZEC2/USDC:USDC``) so they never
    collide with the ``[X-USDC]`` pool.  When GMX retires that pool and its
    replacement is a USDG single-token market, the replacement should take the
    plain ``X/USDC:USDC`` name: that is the pair strategies and whitelists use.

    Rules, applied after the loader has already dropped deprecated, disabled and
    zero-cap markets:

    1. a routed market takes ``X/USDC:USDC`` if no other market owns it;
    2. otherwise it keeps ``X2/USDC:USDC`` unless ``X2`` is excluded or taken,
       in which case it is dropped (the pre-existing behaviour for BTC2/ETH2/SOL2);
    3. every routed market that survives gets ``info["usdc_route"]``.

    Loaders must let routed markets through their ``EXCLUDED_SYMBOLS`` check so
    the decision is made here, once, independent of iteration order.

    :param markets: CCXT markets keyed by unified symbol.
    :param chain: Chain name.
    :param excluded_symbols: The adapter's ``EXCLUDED_SYMBOLS``.
    :return: New markets dict.
    """
    plain: dict[str, dict] = {}
    routed: list[tuple[str, dict, UsdcRoute]] = []
    for symbol, market in markets.items():
        route = get_usdc_route_from_market(market, chain)
        if route is None:
            plain[symbol] = market
        else:
            routed.append((symbol, market, route))

    # Deterministic: process in market-address order.
    routed.sort(key=lambda item: (item[1].get("info", {}).get("market_token") or "").lower())

    result = dict(plain)
    for symbol, market, route in routed:
        base = symbol.split("/")[0]
        canonical = _canonical_base(base)
        canonical_symbol = f"{canonical}/USDC:USDC"
        legacy = f"{canonical}2"
        legacy_symbol = f"{legacy}/USDC:USDC"

        if canonical_symbol not in result and canonical not in excluded_symbols:
            name, unified = canonical, canonical_symbol
        elif legacy not in excluded_symbols and legacy_symbol not in result:
            name, unified = legacy, legacy_symbol
        else:
            logger.info(
                "USDC-routed market %s (%s) dropped: %s is owned by another market and %s is excluded or taken",
                symbol,
                market.get("info", {}).get("market_token"),
                canonical_symbol,
                legacy_symbol,
            )
            continue

        entry = dict(market)
        entry["info"] = dict(market.get("info", {}))
        entry["info"]["usdc_route"] = route.as_info()
        entry["id"] = name
        entry["base"] = name
        entry["baseId"] = name
        entry["symbol"] = unified
        result[unified] = entry
        if unified != symbol:
            logger.info(
                "USDC-routed market %s mapped to %s (collateral %s, swap path %s)",
                entry["info"].get("market_token"),
                unified,
                route.collateral_symbol,
                list(route.swap_path),
            )
    return result


def calculate_swap_min_output(amount_in: int, decimals_in: int, decimals_out: int, slippage: float) -> int:
    """Minimum output of a stable-to-stable swap leg, in output-token units.

    GMX's increase swap checks ``minOutputAmount`` in output-token units.  For a
    1:1 stablecoin leg the floor is the input rescaled to the output decimals,
    minus the order's slippage tolerance.  An off-peg leg then cancels the open
    (collateral refunded) instead of opening with less collateral.

    :param amount_in: Input amount in input-token units.
    :param decimals_in: Input token decimals.
    :param decimals_out: Output token decimals.
    :param slippage: Tolerance as a fraction (0.005 = 0.5%).
    :return: Floor in output-token units (0 for non-positive input).
    :raises ValueError: If slippage is outside [0, 1).
    """
    if not 0 <= slippage < 1:
        raise ValueError(f"slippage must be in [0, 1), got {slippage}")
    if amount_in <= 0:
        return 0
    scaled = amount_in * 10**decimals_out // 10**decimals_in
    # Integer arithmetic on basis points of a basis point to avoid float drift.
    keep_ppm = int(round((1 - slippage) * 1_000_000))
    return scaled * keep_ppm // 1_000_000


def is_zero_open_interest_cap(max_open_interest_long, max_open_interest_short) -> bool:
    """Whether both open-interest caps are explicitly zero.

    GMX retires a pool by setting ``maxOpenInterestLong`` and
    ``maxOpenInterestShort`` to 0; every open is then keeper-cancelled with
    ``MaxOpenInterestExceeded(maxAllowed: 0)`` after the execution fee is paid.
    Missing or unparsable values return ``False`` (cannot tell, keep the market).
    Swap-only markets also have zero caps; callers must only apply this to
    perpetual markets.

    :param max_open_interest_long: Raw cap (int, decimal string or ``None``).
    :param max_open_interest_short: Raw cap (int, decimal string or ``None``).
    :return: ``True`` only when both caps parse as 0.
    """
    try:
        if max_open_interest_long is None or max_open_interest_short is None:
            return False
        return int(max_open_interest_long) == 0 and int(max_open_interest_short) == 0
    except (TypeError, ValueError):
        return False
