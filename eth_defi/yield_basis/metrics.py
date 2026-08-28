"""YieldBasis-native return and redemption diagnostics.

The common vault price remains crvUSD-denominated.  These small pure
functions expose the complementary native-asset series without adding
protocol-specific columns to the shared Parquet schema.  Inputs are raw
onchain integers so callers can keep the exact values in the contextual
DuckDB table and choose their own display precision.
"""

from decimal import Decimal

#: Fixed-point scale used by LT ``pricePerShare()``.
PPS_SCALE: int = 10**18

#: Fixed-point scale used by Curve ``price_oracle()``.
ORACLE_SCALE: int = 10**18

#: Decimal scale used by LT share balances.
LT_SHARE_SCALE: int = 10**18

#: Largest sensible ERC-20 decimal precision for a uint256 raw value.
#: Ethereum's uint256 decimal display has at most 78 decimal places.
MAX_ASSET_DECIMALS: int = 78


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


def asset_crvusd_price(raw_price: int, *, oracle_scale: int = ORACLE_SCALE) -> Decimal:
    """Convert the Curve pool oracle to crvUSD per native asset.

    The reviewed pools quote coin 1, the BTC or ETH asset, in coin 0 crvUSD.

    :param raw_price:
        Raw Curve ``price_oracle()`` value.
    :param oracle_scale:
        Fixed-point scale verified for the deployed pools.
    :return:
        crvUSD value of one whole native asset.
    """

    if raw_price <= 0 or oracle_scale <= 0:
        message = "asset price and its scale must be positive"
        raise ValueError(message)
    return Decimal(raw_price) / oracle_scale


def crvusd_price_per_share(
    raw_price_per_share: int,
    raw_asset_crvusd_price: int,
    *,
    pps_scale: int = PPS_SCALE,
    oracle_scale: int = ORACLE_SCALE,
) -> Decimal:
    """Convert fundamental LT PPS into crvUSD using the Curve oracle.

    The multiplication deliberately includes BTC or ETH price movement in the
    primary stablecoin-denominated performance curve.

    :param raw_price_per_share:
        Raw LT ``pricePerShare()`` value.
    :param raw_asset_crvusd_price:
        Raw Curve asset/crvUSD oracle value from the same block.
    :param pps_scale:
        LT fixed-point scale.
    :param oracle_scale:
        Curve oracle fixed-point scale.
    :return:
        Fundamental crvUSD value of one LT share.
    """

    return asset_price_per_share(raw_price_per_share, pps_scale=pps_scale) * asset_crvusd_price(raw_asset_crvusd_price, oracle_scale=oracle_scale)


def underlying_return(start_raw_price_per_share: int, end_raw_price_per_share: int, *, pps_scale: int = PPS_SCALE) -> Decimal:
    """Return the native-asset endpoint return for one LT share.

    This diagnostic isolates the change in YieldBasis fundamental PPS from the
    BTC or ETH price move included in the primary crvUSD return.

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


def crvusd_return(
    start_raw_price_per_share: int,
    end_raw_price_per_share: int,
    start_raw_asset_crvusd_price: int,
    end_raw_asset_crvusd_price: int,
    *,
    pps_scale: int = PPS_SCALE,
    oracle_scale: int = ORACLE_SCALE,
) -> Decimal:
    """Return the crvUSD endpoint return over identical observation bounds.

    Both endpoints combine LT PPS and the Curve asset/crvUSD oracle so native
    and crvUSD reports can use the same blocks without timing drift.

    :param start_raw_price_per_share:
        Raw LT PPS at the start observation.
    :param end_raw_price_per_share:
        Raw LT PPS at the end observation.
    :param start_raw_asset_crvusd_price:
        Raw asset/crvUSD oracle value at the start observation.
    :param end_raw_asset_crvusd_price:
        Raw asset/crvUSD oracle value at the end observation.
    :param pps_scale:
        LT fixed-point scale.
    :param oracle_scale:
        Curve oracle fixed-point scale.
    :return:
        Decimal endpoint return, where ``0.1`` means 10%.
    """

    start = crvusd_price_per_share(start_raw_price_per_share, start_raw_asset_crvusd_price, pps_scale=pps_scale, oracle_scale=oracle_scale)
    end = crvusd_price_per_share(end_raw_price_per_share, end_raw_asset_crvusd_price, pps_scale=pps_scale, oracle_scale=oracle_scale)
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

    if raw_preview_shares <= 0 or raw_redemption_assets < 0:
        message = "redemption preview values are outside their valid range"
        raise ValueError(message)
    if not 0 <= asset_decimals <= MAX_ASSET_DECIMALS or share_scale <= 0:
        message = "asset decimals or share scale is invalid"
        raise ValueError(message)
    return (Decimal(raw_redemption_assets) / (10**asset_decimals)) / (Decimal(raw_preview_shares) / share_scale)


def temporary_redemption_discount(
    raw_preview_shares: int | None,
    raw_redemption_assets: int | None,
    raw_price_per_share: int,
    *,
    asset_decimals: int,
    pps_scale: int = PPS_SCALE,
    share_scale: int = LT_SHARE_SCALE,
) -> Decimal | None:
    """Return temporary redemption discount relative to fundamental PPS.

    A negative value means the previewed exit is below fundamental value. A
    missing optional preview produces ``None`` rather than hiding the primary
    valuation observation.

    :param raw_preview_shares:
        Raw LT units supplied to ``preview_withdraw``, when available.
    :param raw_redemption_assets:
        Raw underlying units returned by the preview, when available.
    :param raw_price_per_share:
        Raw fundamental LT PPS from the same block.
    :param asset_decimals:
        ERC-20 decimals of the underlying asset.
    :param pps_scale:
        LT PPS fixed-point scale.
    :param share_scale:
        LT share fixed-point scale.
    :return:
        Redemption-to-fundamental relative difference, or ``None`` when the
        preview was unavailable.
    """

    if raw_preview_shares is None or raw_redemption_assets is None:
        return None
    fundamental = asset_price_per_share(raw_price_per_share, pps_scale=pps_scale)
    if fundamental <= 0:
        return None
    return redemption_asset_per_share(raw_preview_shares, raw_redemption_assets, asset_decimals=asset_decimals, share_scale=share_scale) / fundamental - 1


def staked_ratio(raw_effective_supply: int, raw_staked_supply: int) -> Decimal | None:
    """Return staked LT units divided by effective LT supply.

    The ratio is contextual market information only; staked gauge returns and
    YB incentives are outside the unstaked LT performance curve.

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
