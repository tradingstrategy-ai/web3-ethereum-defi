"""Upshift vault offchain metadata.

Upshift's public vault API provides display metadata that is not available from
the vault contracts. In particular, it exposes a vault description,
``hardcoded_strategists`` and named operator wallets. The API does **not**
publish a field named ``curator``. We therefore keep strategist and operator
identities separate and only expose strategists through the generic vault
manager field.

Reference:

- `Upshift vault API documentation <https://docs.upshift.finance/developer-docs/api-reference/vaults>`__
- `Upshift tokenized vault endpoint <https://api.upshift.finance/v1/tokenized_vaults>`__
"""

import datetime
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import NotRequired, TypedDict

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.types import Percent
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Directory for cached Upshift vault API responses.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "upshift"

#: Canonical Upshift public API root.
DEFAULT_API_BASE_URL = "https://api.upshift.finance"

#: Refresh vault metadata every two days.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)

#: Timeout for one Upshift public API request.
DEFAULT_TIMEOUT = 30.0


class UpshiftAPYOverride(TypedDict):
    """Optional APY-display overrides for an Upshift vault.

    Example response: ``{"hardcoded_apy": 0.12,
    "is_show_hardcoded_apy": True}``.
    """

    #: Manual APY shown in the Upshift app. Example: ``0.12`` for 12%.
    hardcoded_apy: NotRequired[Percent | None]

    #: Show compounded APY in the app. Example: ``False``.
    is_show_compound_apy: NotRequired[bool]

    #: Show ``hardcoded_apy`` in the app. Example: ``True``.
    is_show_hardcoded_apy: NotRequired[bool]

    #: Show the vault's target APY in the app. Example: ``False``.
    is_show_target_apy: NotRequired[bool]


class UpshiftPlatformFeeOverride(TypedDict):
    """Platform-fee override configured for an Upshift vault.

    Example response: ``{"management_fee": 0, "is_fee_waived": False}``.
    """

    #: Platform management-fee fraction. Example: ``0``.
    management_fee: Percent | None

    #: Whether Upshift waives the platform fee. Example: ``False``.
    is_fee_waived: bool


class UpshiftInstantRedeemableAsset(TypedDict):
    """One asset accepted by an instant-redemption configuration.

    Example response: ``{"symbol": "USCC", "decimals": 6, "spread_bps": 5}``.
    """

    #: Asset ticker. Example: ``"USCC"``.
    symbol: str

    #: Asset contract address. Example: ``"0x14d60E7FDC0D71d8611742720E4C50E7a974020c"``.
    address: str

    #: Asset decimal places. Example: ``6``.
    decimals: int

    #: Instant-redemption spread in basis points. Example: ``5``.
    spread_bps: int | float


class UpshiftInstantRedeemConfig(TypedDict):
    """Instant-redemption configuration returned for a vault.

    Example response: ``{"output_asset_symbol": "USDC", "is_paused":
    False, "available_liquidity": 1000000000000}``.
    """

    #: Custody subaccount used for instant redemption. Example: ``"0x1E0e...B4b7"``.
    subaccount_address: str

    #: Asset paid to an instant redeemer. Example: ``"USDC"``.
    output_asset_symbol: str

    #: Whether instant redemption is paused. Example: ``False``.
    is_paused: bool

    #: Raw available output-asset liquidity. Example: ``1000000000000``.
    available_liquidity: int | float

    #: Assets that can be redeemed immediately. Example: one ``USCC`` record.
    redeemable_assets: list[UpshiftInstantRedeemableAsset]

    #: Upshift instant-redemption configuration UUID. Example: ``"373efcf4-29de-4c2d-a04c-36607a8a9b90"``.
    id: str

    #: Parent vault UUID. Example: ``"9ae0f5c3-ceb3-4e00-a476-334ccc2c7878"``.
    tokenized_vault_id: str


class UpshiftNAVPricingOverride(TypedDict):
    """One token NAV pricing override keyed by a ``TokenSpec-*`` identifier.

    Example response: ``{"mode": "hardpeg", "value": 1}``.
    """

    #: Pricing method. Example: ``"hardpeg"``.
    mode: str

    #: Price override value. Example: ``1``.
    value: int | float


class UpshiftHistoricalSnapshot(TypedDict):
    """One historical NAV snapshot from the Upshift API.

    Example response: ``{"block_id": 25396895, "asset_share_ratio": 1.0,
    "tvl": 0.0}``.
    """

    #: Vault asset/share ratio. Example: ``1.0``.
    asset_share_ratio: float

    #: Source blockchain block number. Example: ``25396895``.
    block_id: int

    #: Snapshot UUID. Example: ``"fa3d0233-8dec-4a21-814b-b718a581a945"``.
    id: str

    #: Snapshot timestamp. Example: ``"2026-06-25T20:01:28.699921"``.
    snapshot_datetime: str

    #: Raw vault asset balance. Example: ``0.0``.
    total_assets: float

    #: Raw vault share supply. Example: ``0.0``.
    total_shares: float

    #: Upshift-reported TVL in USD. Example: ``0.0``.
    tvl: float

    #: Underlying token USD price. Example: ``0.999701``.
    underlying_price: float


class UpshiftEOAOperator(TypedDict):
    """Named EOA operator wallet linked to an Upshift vault.

    Example response: ``{"name": "NEMO USDC Yield Sub 1",
    "wallet_role": None}``.
    """

    #: Operator wallet address. Example: ``"0xfb1898bB5955FdD11704e397104c6a0e0725EB17"``.
    address: str

    #: Upshift operator UUID. Example: ``"e70a2d03-6570-4d7a-a70b-02781cd5b264"``.
    id: str

    #: Operator display name. Example: ``"NEMO USDC Yield Sub 1"``.
    name: str

    #: Optional assigned wallet role. Example: ``None``.
    wallet_role: str | None


class UpshiftStrategist(TypedDict):
    """Strategy brand configured in Upshift's ``hardcoded_strategists`` list.

    Example response: ``{"strategist_name": "NEMO", "website_url": None}``.
    """

    #: Upshift strategist UUID. Example: ``"73d92b73-e01d-4bf7-90a4-f0a514c12970"``.
    id: str

    #: Strategist logo URL. Example: ``"https://imagedelivery.net/.../public"``.
    strategist_logo: str | None

    #: Strategist brand name. Example: ``"NEMO"``.
    strategist_name: str

    #: Strategist website URL. Example: ``None``.
    website_url: str | None


class UpshiftOperator(TypedDict):
    """Unlabelled operator address and implementation type.

    Example response: ``{"address": "0x1F6d81a390d74a57C314Ef57Ac0cb6749176Cf3E",
    "operator_type": "eoa"}``.
    """

    #: Operator address. Example: ``"0x1F6d81a390d74a57C314Ef57Ac0cb6749176Cf3E"``.
    address: str

    #: Operator implementation type. Example: ``"eoa"``.
    operator_type: str


class UpshiftSubaccount(TypedDict):
    """Custody subaccount assigned to a vault.

    Example response: ``{"address": "0xC8c0Ffb8Ff3BDA26321224e800B3B38AEaB48799",
    "wallet_role": None, "strategist": None}``.
    """

    #: Subaccount address. Example: ``"0xC8c0Ffb8Ff3BDA26321224e800B3B38AEaB48799"``.
    address: str

    #: Optional wallet role. Example: ``None``.
    wallet_role: str | None

    #: Optional associated strategist. Example: ``None``.
    strategist: str | None


class UpshiftReward(TypedDict):
    """Reward programme associated with an Upshift vault.

    Example response: ``{"text": "Upshift Points", "multiplier": 5.0,
    "end_datetime": None}``.
    """

    #: Parent vault UUID. Example: ``"5dd9319f-9f32-4bfe-a9cb-e12899f48ee0"``.
    tokenizedvault_id: str

    #: Reward record creation timestamp. Example: ``"2026-05-14T19:18:14.429000"``.
    created_at: str

    #: Reward UUID. Example: ``"2fe7671c-a857-4676-af72-8aad8ce47b82"``.
    id: str

    #: Reward image URL. Example: ``""``.
    img_url: str | None

    #: Reward programme start timestamp. Example: ``"2026-05-03T20:00:00"``.
    start_datetime: str | None

    #: Reward record update timestamp. Example: ``"2026-05-14T19:18:14.429000"``.
    updated_at: str

    #: Human-readable reward description. Example: ``"Upshift Points"``.
    text: str

    #: Reward multiplier. Example: ``5.0``.
    multiplier: float

    #: Optional reward programme end timestamp. Example: ``None``.
    end_datetime: str | None


class UpshiftComposabilityIntegration(TypedDict):
    """External protocol integration for an Upshift receipt token.

    Example response: ``{"name": "Uniswap", "earning_multiplier": 0.0,
    "description": "Provide liquidity to UniswapV4 AUSD-earnAUSD 0.01%"}``.
    """

    #: Integrated protocol name. Example: ``"Uniswap"``.
    name: str

    #: Human-readable integration description. Example: ``"Provide liquidity to UniswapV4 AUSD-earnAUSD 0.01%"``.
    description: str | None

    #: Additional earning multiplier. Example: ``0.0``.
    earning_multiplier: float

    #: External protocol URL. Example: ``"https://app.merkl.xyz/opportunities/..."``.
    protocol_url: str | None

    #: External protocol logo URL. Example: ``"https://upload.wikimedia.org/.../Uniswap_Logo.svg.png"``.
    logo_url: str | None

    #: Parent vault UUID. Example: ``"672db4d8-72cd-46cb-bde3-746d1dd973a8"``.
    tokenized_vault_id: str

    #: Integration UUID. Example: ``"1f0bddff-84da-4b08-b528-daafb17d8ec9"``.
    id: str


class UpshiftReceiptTokenIntegration(TypedDict):
    """Token integration metadata for an Upshift receipt token.

    Example response: ``{"symbol": "USDT", "chain": 1,
    "is_transferable": True}``.
    """

    #: Integrated token address. Example: ``"0xdAC17F958D2ee523a2206206994597C13D831ec7"``.
    address: str

    #: Integrated token EVM chain ID. Example: ``1``.
    chain: int

    #: Token shorthand. Example: ``"usdt_eth"``.
    shorthand: str | None

    #: Tiingo ticker. Example: ``"usdtusd"``.
    tiingo_ticker: str | None

    #: Whether token transfers are enabled. Example: ``True``.
    is_transferable: bool

    #: Integration update timestamp. Example: ``"2025-03-03T15:40:02.573601"``.
    updated_at: str

    #: Optional external position ID. Example: ``None``.
    position_id: str | None

    #: Token classification. Example: ``"TokenSpec"``.
    token_class: str

    #: Token ticker. Example: ``"USDT"``.
    symbol: str

    #: Token image URL. Example: ``"https://coin-images.coingecko.com/.../Tether.png"``.
    img_url: str | None

    #: Integration UUID. Example: ``"a86f126b-f951-4a00-adb5-14411e691675"``.
    id: str

    #: Integration creation timestamp. Example: ``"2024-06-27T15:17:27.208246"``.
    created_at: str

    #: Optional stable-token pair UUID. Example: ``"b90ac83f-5a4a-475e-8cca-ec81847ef842"``.
    stable_token_pair_id: str | None


class UpshiftReportedAPY(TypedDict):
    """Current APY breakdown reported by Upshift.

    Example response: ``{"apy": 0.1, "underlying_apy": 0.0,
    "rewards_compounded": 0.0}``.
    """

    #: Underlying strategy APY. Example: ``0.0``.
    underlying_apy: Percent | None

    #: APY from compounded rewards. Example: ``0.0``.
    rewards_compounded: Percent | None

    #: Liquid reward APY. Example: ``0.0``.
    liquid_apy: Percent | None

    #: Report UUID. Example: ``"78d43044-2c0a-40dd-bbcd-043750ecc38f"``.
    id: str

    #: Report update timestamp. Example: ``"2025-11-20T18:38:43.474000"``.
    updated_at: str

    #: Parent vault UUID. Example: ``"672db4d8-72cd-46cb-bde3-746d1dd973a8"``.
    tokenized_vault_id: str

    #: Total reported APY. Example: ``0.1`` for 10%.
    apy: Percent | None

    #: APY from claimable rewards. Example: ``0.0``.
    rewards_claimable: Percent | None

    #: Optional APY methodology explanation. Example: ``""``.
    explainer: str | None

    #: Report creation timestamp. Example: ``"2025-11-20T18:38:43.474000"``.
    created_at: str


class UpshiftSolanaVaultMetadata(TypedDict):
    """Solana-specific metadata returned for an Upshift vault.

    Example response: ``{"deposit_token_symbol": "USDC",
    "deposit_token_decimals": 6}``.
    """

    #: Solana vault state PDA. Example: ``"HegTiqVxUvnh3fD9ZA2v7PF3XnoqHgz4ytJRZKdLJ5ra"``.
    vault_state_pda: str

    #: Solana receipt-token mint. Example: ``"CnhPtD2gHHrUvfuA6HrDdLQBKjGgVL8HZMJNCZdXuWEs"``.
    share_mint: str

    #: Solana denomination-token mint. Example: ``"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"``.
    deposit_mint: str

    #: Denomination-token ticker. Example: ``"USDC"``.
    deposit_token_symbol: str

    #: Denomination-token decimal places. Example: ``6``.
    deposit_token_decimals: int

    #: Upshift Solana programme ID. Example: ``"up12bytoZBmwofqsySf2uqKQ7zpfeKiAWwfvqzJjtRt"``.
    program_id: str

    #: Solana metadata UUID. Example: ``"8de00f2c-f8c9-4471-a038-6484eca9a067"``.
    id: str

    #: Parent vault UUID. Example: ``"09bd056e-5fd5-47e9-a655-6e844c492341"``.
    tokenized_vault_id: str


class UpshiftStellarVaultMetadata(TypedDict):
    """Stellar-specific metadata returned for an Upshift vault.

    Example response: ``{"deposit_token_symbol": "USDC", "network_name":
    "mainnet"}``.
    """

    #: Stellar denomination-token address. Example: ``"CCW67TSZV3SSS2HXMBQ5JFGCKJNXKZM7UQUWUZPUTHXSTZLEO7SJMI75"``.
    deposit_token_address: str

    #: Denomination-token ticker. Example: ``"USDC"``.
    deposit_token_symbol: str

    #: Denomination-token decimal places. Example: ``7``.
    deposit_token_decimals: int

    #: Stellar network name. Example: ``"mainnet"``.
    network_name: str

    #: Utila vault identifier. Example: ``"vaults/7a9612947ff0"``.
    utila_vault_id: str

    #: Utila wallet identifier. Example: ``"wallets/d1ab1d873291"``.
    utila_wallet_id: str

    #: Stellar metadata UUID. Example: ``"0d6aee1e-37b5-412d-8e99-b050c0f806e5"``.
    id: str

    #: Parent vault UUID. Example: ``"139fab4b-278d-4f1a-b338-cfcb67b01921"``.
    tokenized_vault_id: str


class UpshiftVaultAPIResponse(TypedDict):
    """Complete raw response from Upshift's tokenized-vault endpoint.

    All 61 documented top-level fields were observed in the live response for
    NEMO USDC Prime on 2026-07-23. Nested structures are modelled by the
    associated ``Upshift*`` TypedDicts above.

    Reference:

    - `Upshift vault API documentation <https://docs.upshift.finance/developer-docs/api-reference/vaults>`__
    """

    #: Vault deployment address. Example: ``"0x955256B31097dDf47a9E47A95aDfDFB4460D8522"``.
    address: str

    #: Optional yield-distributor address. Example: ``None``.
    yield_distributor: str | None

    #: EVM chain ID. Example: ``1`` for Ethereum mainnet.
    chain: int

    #: Vault strategy description. Example: ``"NEMO USDC Prime is an automated, multi-strategy..."``.
    description: str | None

    #: Upshift implementation family. Example: ``"multiAssetVault"``.
    internal_type: str

    #: Marketplace product category. Example: ``"DeFi Yield"``.
    public_type: str | None

    #: Whether Upshift highlights the vault. Example: ``False``.
    is_featured: bool

    #: Whether Upshift displays the vault publicly. Example: ``True``.
    is_visible: bool | None

    #: Weekly performance fee in basis points. Example: ``20.0``.
    weekly_performance_fee_bps: float | None

    #: Optional platform-fee override. Example: ``{"management_fee": 0, "is_fee_waived": False}``.
    platform_fee_override: UpshiftPlatformFeeOverride | None

    #: Vault creation timestamp. Example: ``"2026-06-25T10:19:00"``.
    start_datetime: str | None

    #: Upshift display name. Example: ``"NEMO USDC Prime"``.
    vault_name: str | None

    #: Target reserve amount. Example: ``0.0``.
    reserve_target: float | None

    #: Allowed reserve variation. Example: ``0.0``.
    reserve_tolerance: float | None

    #: Withdrawal-alert threshold. Example: ``None``.
    withdrawal_alert_threshold: float | None

    #: Withdrawal-alert channel selection. Example: ``"both"``.
    withdrawal_alert_channels: str | None

    #: Vault lifecycle status. Example: ``"active"``.
    status: str

    #: Whether fee charging requires a manual action. Example: ``False``.
    is_charge_fees_manual: bool

    #: Receipt-token symbol. Example: ``"NEMO USDC Yield"``.
    receipt_token_symbol: str | None

    #: Whether external asset updates are enabled. Example: ``False``.
    enable_external_assets_update: bool

    #: Whether the vault wraps distributor fees. Example: ``False``.
    is_distributor_fee_wrapper: bool

    #: Vault logo URL. Example: ``"https://imagedelivery.net/.../nemoicon-light.svg/public"``.
    vault_logo_url: str | None

    #: Risk label set by Upshift. Example: ``None``.
    risk: str | None

    #: Maximum daily drawdown fraction. Example: ``None``.
    max_daily_drawdown: Percent | None

    #: Chain family. Example: ``"evm"``.
    chain_type: str

    #: Historical APY windows in days. Example: ``[7, 30]``.
    enabled_historical_price_horizons: list[int]

    #: Whether Upshift spotlights the vault. Example: ``False``.
    is_spotlighted: bool

    #: Whether Upshift shows a filled-cap indicator. Example: ``False``.
    show_cap_filled: bool

    #: Whether only withdrawals are currently enabled. Example: ``False``.
    withdrawal_only: bool

    #: Historical simple APY keyed by horizon days. Example: ``{"7": 0.31189842638050264}``.
    historical_apy: dict[str, Percent]

    #: Default APY horizon in days. Example: ``7``.
    default_apy_horizon: int | None

    #: Management-fee waiver expiry timestamp. Example: ``None``.
    management_fee_waived_until_date: str | None

    #: Management-fee waiver TVL threshold. Example: ``None``.
    management_fee_waived_until_tvl: float | None

    #: Performance-fee waiver expiry timestamp. Example: ``None``.
    performance_fee_waived_until_date: str | None

    #: Performance-fee waiver TVL threshold. Example: ``None``.
    performance_fee_waived_until_tvl: float | None

    #: NAV base-token metadata identifier. Example: ``None``.
    nav_base_asset_token_id: str | None

    #: Token pricing overrides keyed by ``TokenSpec-*``. Example: ``{"TokenSpec-1-0xA0b8...": {"mode": "hardpeg", "value": 1}}``.
    nav_pricing_overrides: dict[str, UpshiftNAVPricingOverride] | None

    #: Optional APY display overrides. Example: ``{"hardcoded_apy": 0.12, "is_show_hardcoded_apy": True}``.
    apy_override: UpshiftAPYOverride | None

    #: Largest historical drawdown fraction. Example: ``0.0002156861650454448``.
    max_drawdown: Percent | None

    #: Latest reported TVL in USD. Example: ``3591665.1170049477``.
    latest_reported_tvl: float | None

    #: Campaign APY override. Example: ``None``.
    campaign_apy: Percent | None

    #: Metrics calculation timestamp. Example: ``"2026-07-23T17:01:41.120995"``.
    metrics_last_updated: str | None

    #: Upshift vault UUID. Example: ``"58c28ee6-aff6-49e0-9291-625f9f82e90f"``.
    id: str

    #: Solana-specific metadata. Example: ``None`` for an EVM vault.
    solana_vault_metadata: UpshiftSolanaVaultMetadata | None

    #: Stellar-specific metadata. Example: ``None`` for an EVM vault.
    stellar_vault_metadata: UpshiftStellarVaultMetadata | None

    #: Optional instant-redemption configuration. Example: ``None``.
    instant_redeem_config: UpshiftInstantRedeemConfig | None

    #: App visibility mode. Example: ``"all"``.
    view_type: str | None

    #: Reward programmes. Example: one ``{"text": "Upshift Points", "multiplier": 5.0}`` record.
    rewards: list[UpshiftReward]

    #: Current APY breakdown. Example: ``None``.
    reported_apy: UpshiftReportedAPY | None

    #: Receipt-token integration metadata. Example: ``[]``.
    receipt_token_integrations: list[UpshiftReceiptTokenIntegration]

    #: Strategy brands. Example: one ``{"strategist_name": "NEMO"}`` record.
    hardcoded_strategists: list[UpshiftStrategist]

    #: External composability integrations. Example: ``[]``.
    composability_integrations: list[UpshiftComposabilityIntegration]

    #: API cache timestamp. Example: ``None``.
    cached_at: str | None

    #: Historical NAV snapshots. Example: one ``{"block_id": 25396895, "tvl": 0.0}`` record.
    historical_snapshots: list[UpshiftHistoricalSnapshot]

    #: Vault custody subaccounts. Example: ``[]``.
    subaccounts: list[UpshiftSubaccount]

    #: Named EOA operator wallets. Example: one ``{"name": "NEMO USDC Yield Sub 1"}`` record.
    eoa_operators: list[UpshiftEOAOperator]

    #: Historical compounded APY keyed by horizon days. Example: ``{"7": 0.3647473251285198}``.
    historical_compound_apy: dict[str, Percent]

    #: Current reported TVL in USD. Example: ``3591665.1170049477``.
    tvl: float | None

    #: Return per share keyed by horizon days. Example: ``{"7": 0.005170042919987905}``.
    pnl_per_share: dict[str, Percent]

    #: Daily return per share keyed by timestamp. Example: ``{"2026-07-23T00:00:00": 0.0008179999999999854}``.
    daily_pnl_per_share: dict[str, Percent]

    #: Contract operators. Example: one ``{"operator_type": "eoa"}`` record.
    operators: list[UpshiftOperator]


class UpshiftVaultMetadata(TypedDict):
    """Normalised public metadata for one Upshift vault.

    The data is sourced from ``GET /v1/tokenized_vaults/{address}``. Upshift's
    ``hardcoded_strategists`` identifies a strategy brand, not a curator role,
    so it is represented as ``strategist_names`` rather than mislabelled as
    curator data.

    Reference:

    - `Upshift vault API documentation <https://docs.upshift.finance/developer-docs/api-reference/vaults>`__
    """

    #: EVM chain ID reported by Upshift.
    chain_id: int

    #: Checksummed vault contract address.
    vault_address: HexAddress

    #: Display name configured in Upshift, if any.
    name: str | None

    #: Full strategy description configured in Upshift, if any.
    description: str | None

    #: Strategy brands from the API's ``hardcoded_strategists`` records.
    strategist_names: tuple[str, ...]

    #: Human-readable names of EOA operator wallets.
    operator_names: tuple[str, ...]

    #: Vault lifecycle status, such as ``"active"`` or ``"closed"``.
    status: str | None

    #: Upshift implementation family, such as ``"multiAssetVault"``.
    internal_type: str | None

    #: Whether Upshift currently displays the vault in its frontend.
    is_visible: bool | None


def _parse_string(raw_value: object) -> str | None:
    """Normalise an optional API text field.

    :param raw_value:
        Untyped field from an Upshift JSON response.

    :return:
        Stripped text, or ``None`` when the API did not provide meaningful
        text.
    """

    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    return value or None


def _parse_named_records(raw_records: object, field_name: str) -> tuple[str, ...]:
    """Extract distinct display names from an Upshift API record list.

    :param raw_records:
        Untyped list field from the API response.

    :param field_name:
        Display-name field to extract from each record.

    :return:
        Distinct non-empty display names in API order.
    """

    if not isinstance(raw_records, list):
        return ()

    names = (name for record in raw_records if isinstance(record, dict) and (name := _parse_string(record.get(field_name))) is not None)
    return tuple(dict.fromkeys(names))


def _parse_upshift_vault_metadata(raw_metadata: dict) -> UpshiftVaultMetadata:
    """Normalise one public Upshift vault API response.

    :param raw_metadata:
        JSON object returned by ``GET /v1/tokenized_vaults/{address}``.

    :return:
        Metadata with checked addresses and typed identity fields.
    """

    chain_id = raw_metadata.get("chain")
    if not isinstance(chain_id, int):
        raise ValueError(f"Upshift vault metadata has invalid chain: {chain_id!r}")

    address = _parse_string(raw_metadata.get("address"))
    if address is None:
        message = "Upshift vault metadata has no address"
        raise ValueError(message)

    is_visible = raw_metadata.get("is_visible")
    return UpshiftVaultMetadata(
        chain_id=chain_id,
        vault_address=Web3.to_checksum_address(address),
        name=_parse_string(raw_metadata.get("vault_name")),
        description=_parse_string(raw_metadata.get("description")),
        strategist_names=_parse_named_records(raw_metadata.get("hardcoded_strategists"), "strategist_name"),
        operator_names=_parse_named_records(raw_metadata.get("eoa_operators"), "name"),
        status=_parse_string(raw_metadata.get("status")),
        internal_type=_parse_string(raw_metadata.get("internal_type")),
        is_visible=is_visible if isinstance(is_visible, bool) else None,
    )


def _read_cached_metadata(cache_file: Path) -> UpshiftVaultMetadata | None:
    """Read and parse a previously cached Upshift API response.

    :param cache_file:
        JSON response cache path.

    :return:
        Parsed metadata, or ``None`` when no valid cached response exists.
    """

    if not cache_file.exists() or cache_file.stat().st_size == 0:
        return None

    try:
        with cache_file.open("rt") as file:
            raw_metadata = json.load(file)
    except (JSONDecodeError, OSError) as error:
        logger.warning("Could not read Upshift vault metadata cache %s: %s", cache_file, error)
        return None

    if not isinstance(raw_metadata, dict):
        logger.warning("Upshift vault metadata cache %s did not contain an object", cache_file)
        return None

    try:
        return _parse_upshift_vault_metadata(raw_metadata)
    except ValueError as error:
        logger.warning("Could not parse Upshift vault metadata cache %s: %s", cache_file, error)
        return None


def _is_cache_fresh(cache_file: Path, now_: datetime.datetime, max_cache_duration: datetime.timedelta) -> bool:
    """Check whether an Upshift vault cache file is still current.

    :param cache_file:
        JSON response cache path.

    :param now_:
        Current naive UTC time.

    :param max_cache_duration:
        Maximum cache age.

    :return:
        ``True`` when the cache exists, is non-empty and is recent enough.
    """

    if not cache_file.exists() or cache_file.stat().st_size == 0:
        return False

    modified_at = native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime)
    return now_ - modified_at <= max_cache_duration


def fetch_upshift_vault_metadata(
    web3: Web3,
    vault_address: HexAddress | str,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
    timeout: float = DEFAULT_TIMEOUT,
) -> UpshiftVaultMetadata | None:
    """Fetch cached public metadata for one Upshift vault.

    Reads ``GET /v1/tokenized_vaults/{address}`` from Upshift's public API and
    caches its raw response by EVM chain and vault address. A failed refresh
    returns a valid stale cache when one exists, so a temporary API outage does
    not remove already-discovered vault descriptions or strategist identities.

    The API exposes named strategists and EOA operators, but no explicit
    curator field. Callers must not interpret either as a verified curator
    record without separate registry evidence.

    :param web3:
        Web3 connection used only to obtain the EVM chain ID.

    :param vault_address:
        Upshift vault deployment address.

    :param cache_path:
        Directory for cached raw JSON responses.

    :param api_base_url:
        Upshift API root. Override this with a test server URL in tests.

    :param now_:
        Optional current naive UTC timestamp for cache-expiry tests.

    :param max_cache_duration:
        Maximum age of a cache response before refreshing it.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        Parsed vault metadata, or ``None`` when Upshift has no accessible
        matching record.
    """

    assert isinstance(cache_path, Path), "cache_path must be a Path"

    chain_id = web3.eth.chain_id
    checksummed_address = Web3.to_checksum_address(vault_address)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = (cache_path / f"upshift_vault_{chain_id}_{checksummed_address.lower()}.json").resolve()
    now_ = now_ or native_datetime_utc_now()

    with wait_other_writers(cache_file):
        cached_metadata = _read_cached_metadata(cache_file)
        if cached_metadata is not None and _is_cache_fresh(cache_file, now_, max_cache_duration):
            return cached_metadata

        api_url = f"{api_base_url.rstrip('/')}/v1/tokenized_vaults/{checksummed_address}"
        try:
            response = requests.get(api_url, timeout=timeout)
            response.raise_for_status()
            raw_metadata = json.loads(response.text)
            if not isinstance(raw_metadata, dict):
                raise ValueError(f"Upshift vault API returned {type(raw_metadata)} instead of an object")
            metadata = _parse_upshift_vault_metadata(raw_metadata)
        except (requests.RequestException, JSONDecodeError, ValueError) as error:
            if cached_metadata is not None:
                logger.warning("Could not refresh Upshift vault metadata for %s, using stale cache: %s", checksummed_address, error)
                return cached_metadata

            logger.warning("Could not fetch Upshift vault metadata for %s: %s", checksummed_address, error)
            return None

        if metadata["chain_id"] != chain_id:
            logger.warning(
                "Upshift vault metadata for %s belongs to chain %d, not requested chain %d",
                checksummed_address,
                metadata["chain_id"],
                chain_id,
            )
            return None

        with cache_file.open("wt") as file:
            json.dump(raw_metadata, file)

        return metadata
