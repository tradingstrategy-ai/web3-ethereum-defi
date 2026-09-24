"""YieldBasis fundamental, redemption and conversion calculations.

The primary share price uses the USD value returned by ``preview_withdraw``;
fundamental ``pricePerShare`` remains available for comparison. Inputs stay as
raw onchain integers until these pure functions derive display values.

Stablecoin conversion is an endpoint cost, exposed separately through
:func:`estimate_usd_stablecoin_swap_cost` and never embedded in the historical
equity curve.
"""

from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.types import Percent

#: Fixed-point scale used by LT ``pricePerShare()``.
PPS_SCALE: int = 10**18

#: Fixed-point scale used by Curve ``price_oracle()``.
ORACLE_SCALE: int = 10**18

#: Decimal scale used by LT share balances.
LT_SHARE_SCALE: int = 10**18

#: Assumed one-way conversion cost between a generic USD stablecoin and the
#: YieldBasis underlying asset: 10 basis points, or 0.10%.
YIELD_BASIS_USD_STABLECOIN_SWAP_COST: Percent = 0.001

#: Largest sensible ERC-20 decimal precision for a uint256 raw value.
#: Ethereum's uint256 decimal display has at most 78 decimal places.
MAX_ASSET_DECIMALS: int = 78


def estimate_usd_stablecoin_swap_cost(underlying_token: HexAddress) -> Percent:
    """Estimate one generic USD-stablecoin/underlying conversion cost.

    The current baseline is deliberately simple: every reviewed WBTC, cbBTC,
    tBTC and WETH market uses the same fixed 10-basis-point assumption. The
    underlying-token argument keeps the accounting boundary explicit and
    allows later token-specific estimates without changing the VaultBase fee
    interface or historical schema. This estimate excludes price impact, gas
    and MEV.

    :param underlying_token:
        YieldBasis LT underlying ERC-20 address.
    :return:
        One-way cost as a fraction, currently ``0.001`` for every token.
    """

    if not underlying_token:
        message = "YieldBasis underlying token address is required"
        raise ValueError(message)
    return YIELD_BASIS_USD_STABLECOIN_SWAP_COST


def usd_stablecoin_investor_return(fundamental_entry_share_price: Decimal, redemption_exit_share_price: Decimal, underlying_token: HexAddress) -> Decimal:
    """Calculate mint-to-redemption USD return after endpoint conversions.

    YieldBasis mints new shares at fundamental PPS and redeems them at the
    current ``preview_withdraw`` value. The entry value must therefore exclude
    TRD, while the exit value includes it. Each stablecoin conversion retains
    ``1 - cost`` of value. The fixed cost is applied once at entry and once at
    exit, never at intermediate historical observations.

    :param fundamental_entry_share_price:
        Positive fundamental USD PPS used to mint at entry.
    :param redemption_exit_share_price:
        Positive TRD-inclusive USD redemption value at exit.
    :param underlying_token:
        YieldBasis LT underlying ERC-20 address.
    :return:
        Net endpoint return where ``0.1`` means 10%.
    """

    if fundamental_entry_share_price <= 0 or redemption_exit_share_price <= 0:
        message = "USD entry and exit share-price equivalents must be positive"
        raise ValueError(message)
    conversion_multiplier = Decimal(1) - Decimal(str(estimate_usd_stablecoin_swap_cost(underlying_token)))
    return conversion_multiplier * (redemption_exit_share_price / fundamental_entry_share_price) * conversion_multiplier - 1


def round_trip_usd_stablecoin_swap_cost(underlying_token: HexAddress) -> Decimal:
    """Return the assumed loss from an otherwise flat round trip.

    :param underlying_token:
        YieldBasis LT underlying ERC-20 address.
    :return:
        Positive loss fraction, currently ``0.001999`` for two 10-bps legs.
    """

    conversion_multiplier = Decimal(1) - Decimal(str(estimate_usd_stablecoin_swap_cost(underlying_token)))
    return Decimal(1) - conversion_multiplier * conversion_multiplier


def asset_price_per_share(raw_price_per_share: int, *, pps_scale: int = PPS_SCALE) -> Decimal:
    """Convert raw LT ``pricePerShare`` to native asset units.

    :param raw_price_per_share:
        LT fixed-point ``pricePerShare`` value.
    :param pps_scale:
        Fixed-point scale verified for the deployed market.
    :return:
        Native-asset value of one LT share.
    """

    if raw_price_per_share <= 0 or pps_scale <= 0:
        message = "pricePerShare and its scale must be positive"
        raise ValueError(message)
    return Decimal(raw_price_per_share) / pps_scale


def asset_usd_price(raw_price: int, *, oracle_scale: int = ORACLE_SCALE) -> Decimal:
    """Convert the Curve pool oracle to assumed USD per native asset.

    The reviewed pools quote coin 1, the BTC or ETH asset, in coin 0 crvUSD.
    The integration treats that stable-side quote as one USD for its generic
    USD comparison; crvUSD remains protocol plumbing, not the investor-facing
    denomination token.

    :param raw_price:
        Raw Curve ``price_oracle()`` value.
    :param oracle_scale:
        Fixed-point scale verified for the deployed pools.
    :return:
        Assumed USD value of one whole native asset.
    """

    if raw_price <= 0 or oracle_scale <= 0:
        message = "asset price and its scale must be positive"
        raise ValueError(message)
    return Decimal(raw_price) / oracle_scale


def underlying_return(start_raw_price_per_share: int, end_raw_price_per_share: int, *, pps_scale: int = PPS_SCALE) -> Decimal:
    """Return the native-asset endpoint return for one LT share.

    This diagnostic isolates the change in YieldBasis fundamental PPS from the
    BTC or ETH price move included in the primary USD return.

    :param start_raw_price_per_share:
        Raw LT PPS at the start observation.
    :param end_raw_price_per_share:
        Raw LT PPS at the end observation.
    :param pps_scale:
        LT fixed-point scale.
    :return:
        Decimal endpoint return, where ``0.1`` means 10%.
    """

    start = asset_price_per_share(start_raw_price_per_share, pps_scale=pps_scale)
    end = asset_price_per_share(end_raw_price_per_share, pps_scale=pps_scale)
    return end / start - 1


def redemption_asset_per_share(
    raw_preview_shares: int,
    raw_redemption_assets: int,
    *,
    asset_decimals: int,
    share_scale: int = LT_SHARE_SCALE,
) -> Decimal:
    """Convert a protocol redemption preview to native asset per LT share.

    ``preview_withdraw`` returns raw units of the market's volatile asset,
    whereas LT shares use the protocol's 18-decimal fixed-point scale.

    :param raw_preview_shares:
        Raw LT units supplied to ``preview_withdraw``.
    :param raw_redemption_assets:
        Raw underlying units returned by the preview.
    :param asset_decimals:
        ERC-20 decimals of the underlying asset.
    :param share_scale:
        LT share fixed-point scale.
    :return:
        Native-asset redemption value per whole LT share.
    """

    if raw_preview_shares <= 0 or raw_redemption_assets <= 0:
        message = "redemption preview values are outside their valid range"
        raise ValueError(message)
    if not 0 <= asset_decimals <= MAX_ASSET_DECIMALS or share_scale <= 0:
        message = "asset decimals or share scale is invalid"
        raise ValueError(message)
    return (Decimal(raw_redemption_assets) / (10**asset_decimals)) / (Decimal(raw_preview_shares) / share_scale)


def redemption_usd_price_per_share(
    raw_preview_shares: int,
    raw_redemption_assets: int,
    raw_asset_crvusd_price: int,
    *,
    asset_decimals: int,
    oracle_scale: int = ORACLE_SCALE,
    share_scale: int = LT_SHARE_SCALE,
) -> Decimal:
    """Convert a marginal redemption preview to USD per LT share.

    This is the primary gross YieldBasis share-price-equivalence formula used
    by the common vault pipeline. The first term incorporates the Temporary
    Redemption Discount (TRD); the second term incorporates BTC or ETH price
    volatility. Multiplying the two is equivalent to fundamental USD PPS
    multiplied by ``1 + TRD``, so callers must not subtract TRD a second time.
    Investor-specific conversion costs are not deducted here: use
    :func:`usd_stablecoin_investor_return` for a fee-adjusted investment period.

    :param raw_preview_shares:
        Raw LT units supplied to ``preview_withdraw``.
    :param raw_redemption_assets:
        Raw underlying units returned by the preview.
    :param raw_asset_crvusd_price:
        Raw Curve asset/crvUSD oracle value used as the USD proxy at the same
        block.
    :param asset_decimals:
        ERC-20 decimal precision of the underlying asset.
    :param oracle_scale:
        Curve oracle fixed-point scale.
    :param share_scale:
        LT share fixed-point scale.
    :return:
        Gross marginal redemption value of one whole LT share in USD.
    """

    native_redemption_value = redemption_asset_per_share(
        raw_preview_shares,
        raw_redemption_assets,
        asset_decimals=asset_decimals,
        share_scale=share_scale,
    )
    return native_redemption_value * asset_usd_price(raw_asset_crvusd_price, oracle_scale=oracle_scale)


def temporary_redemption_discount(
    raw_preview_shares: int,
    raw_redemption_assets: int,
    raw_price_per_share: int,
    *,
    asset_decimals: int,
    pps_scale: int = PPS_SCALE,
    share_scale: int = LT_SHARE_SCALE,
) -> Decimal:
    """Return temporary redemption discount relative to fundamental PPS.

    A negative value means the previewed exit is below fundamental value. The
    preview is required because the primary curve must not substitute
    fundamental value and mix two accounting bases.

    :param raw_preview_shares:
        Raw LT units supplied to ``preview_withdraw``.
    :param raw_redemption_assets:
        Raw underlying units returned by the preview.
    :param raw_price_per_share:
        Raw fundamental LT PPS from the same block.
    :param asset_decimals:
        ERC-20 decimals of the underlying asset.
    :param pps_scale:
        LT PPS fixed-point scale.
    :param share_scale:
        LT share fixed-point scale.
    :return:
        Redemption-to-fundamental relative difference.
    """

    fundamental = asset_price_per_share(raw_price_per_share, pps_scale=pps_scale)
    return redemption_asset_per_share(raw_preview_shares, raw_redemption_assets, asset_decimals=asset_decimals, share_scale=share_scale) / fundamental - 1


def staked_ratio(raw_effective_supply: int, raw_staked_supply: int) -> Decimal | None:
    """Return staked LT units divided by effective LT supply.

    The ratio is contextual market information only; staked gauge returns and
    YB incentives are outside the base LT performance curve.

    :param raw_effective_supply:
        Effective LT supply from ``updated_balances()``.
    :param raw_staked_supply:
        Effective LT units held in the staker.
    :return:
        Ratio from zero to one, or ``None`` for zero supply.
    """

    if raw_effective_supply <= 0:
        return None
    if raw_staked_supply < 0 or raw_staked_supply > raw_effective_supply:
        message = "staked supply must be between zero and effective supply"
        raise ValueError(message)
    return Decimal(raw_staked_supply) / raw_effective_supply
