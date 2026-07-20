"""Read-only adapter for Sygnum Desygnate FILQ fund shares.

FILQ is a permissioned SygToken ERC-20, not an ERC-4626 vault.  Authoritative
sources: https://www.sygnum.com/filq/ and the exact verified implementation
https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all.

NAV/share is published by Chainlink bundle feeds.  Their accepted reports are
available as ``BundleReportUpdated`` events from the shared DataFeedsCache.
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.chainlink.bundle_aggregator import create_bundle_aggregator_proxy, decode_bundle_decimal, fetch_chainlink_latest_bundle
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.sygnum.constants import FILQ_BUNDLE_AGGREGATOR_ADDRESS, FILQ_BUNDLE_DATA_ID_BY_TOKEN, FILQ_BUNDLE_DECIMALS_BY_TOKEN, FILQ_BUNDLE_FIRST_SEEN_AT_BLOCK_BY_TOKEN, FILQ_BUNDLE_PROXY_BY_TOKEN, FILQ_CURATOR_SLUG, FILQ_FIRST_SEEN_AT_BLOCK_BY_TOKEN, FILQ_MANAGER_NAME, FILQ_NAV_BUNDLE_INDEX, SYGNUM_ETHEREUM_CHAIN_ID
from eth_defi.tokenised_fund.sygnum.historical import SygnumVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

SYGNUM_RESTRICTED_FLOW_REASON = "FILQ subscriptions, transfers and redemptions require Sygnum-approved wallets and issuer-controlled settlement"


class SygnumVaultInfo(VaultInfo, total=False):
    """FILQ scan metadata."""

    token: HexAddress
    chain_id: int
    synthetic_usd_denomination: bool
    nav_source: str
    nav_available: bool


def export_sygnum_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export non-transferable USD accounting metadata.

    :param chain_id: Chain hosting the FILQ proxy.
    :return: Synthetic denomination metadata.
    """

    return {"address": None, "chain": chain_id, "name": "United States Dollar", "symbol": "USD", "decimals": None, "total_supply": None, "extra_data": {"synthetic": True}}


class SygnumVault(TokenisedFundVault):
    """Read-only adapter for reviewed Sygnum FILQ share classes.

    ERC-20 supply is combined with the reviewed FILQ Chainlink bundle NAV.
    Public dealing flows remain unavailable because every transfer and
    settlement is subject to the Sygnum permission manager.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a FILQ adapter.

        :param web3: Web3 connection to Ethereum.
        :param spec: Chain and FILQ share-token address.
        :param token_cache: Shared token metadata cache.
        :param features: Classification flags.
        :param default_block_identifier: Default metadata block.
        :param require_denomination_token: Retained for :class:`VaultBase` compatibility.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        token_address = HexAddress(spec.vault_address.lower())
        if spec.chain_id != SYGNUM_ETHEREUM_CHAIN_ID or token_address not in FILQ_BUNDLE_PROXY_BY_TOKEN:
            raise ValueError(f"Unsupported Sygnum FILQ product: chain={spec.chain_id}, token={spec.vault_address}")
        self.web3, self.spec, self.features = web3, spec, features or {ERC4626Feature.sygnum_like}
        self.default_block_identifier = default_block_identifier
        self.first_seen_at_block = FILQ_FIRST_SEEN_AT_BLOCK_BY_TOKEN[token_address]
        self.oracle_first_seen_at_block = FILQ_BUNDLE_FIRST_SEEN_AT_BLOCK_BY_TOKEN[token_address]

    @property
    def chain_id(self) -> int:
        """Return the configured EVM chain id."""
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum FILQ share-token address."""
        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""
        return self.address

    @property
    def price_feed_address(self) -> HexAddress:
        """Return the reviewed FILQ Chainlink bundle proxy address."""

        return HexAddress(Web3.to_checksum_address(FILQ_BUNDLE_PROXY_BY_TOKEN[HexAddress(self.spec.vault_address.lower())]))

    @cached_property
    def price_feed_contract(self) -> Contract:
        """Return the FILQ Chainlink bundle proxy contract."""

        return create_bundle_aggregator_proxy(self.web3, self.price_feed_address)

    @property
    def bundle_data_id(self) -> bytes:
        """Return the reviewed Chainlink data identifier for this share class."""

        return FILQ_BUNDLE_DATA_ID_BY_TOKEN[HexAddress(self.spec.vault_address.lower())]

    @property
    def bundle_decimals(self) -> tuple[int, ...]:
        """Return the reviewed FILQ bundle decimal metadata."""

        return FILQ_BUNDLE_DECIMALS_BY_TOKEN[HexAddress(self.spec.vault_address.lower())]

    @property
    def name(self) -> str:
        """Return the on-chain FILQ class name."""
        return self.share_token.name or "Fidelity USD Digital Liquidity Fund"

    @property
    def symbol(self) -> str:
        """Return the on-chain FILQ class symbol."""
        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Return a plain-language product description."""
        return "Fidelity International USD Digital Liquidity Fund share token issued through Sygnum Desygnate."

    @property
    def short_description(self) -> str | None:
        """Return the compact listing description."""
        return "Permissioned Fidelity International USD liquidity-fund share"

    @property
    def manager_name(self) -> str | None:
        """Return FILQ's investment manager."""
        return FILQ_MANAGER_NAME

    @property
    def curator_slug(self) -> str:
        """Return FILQ's curator metadata identifier."""
        return FILQ_CURATOR_SLUG

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the FILQ ERC-20 address.

        :param block_identifier: Ignored compatibility parameter.
        :return: Share-token address.
        """
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch FILQ ERC-20 metadata.

        :return: Token details for the share class.
        """
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Sygnum FILQ share token {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no ERC-20 asset because FILQ is not ERC-4626.

        :return: Always ``None``.
        """
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no transferable denomination token.

        :return: Always ``None``.
        """
        return None

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read outstanding FILQ shares.

        :param block_identifier: Historical block identifier.
        :return: Decimal-scaled total supply.
        """
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def decode_bundle_nav(self, bundle: bytes, bundle_decimals: tuple[int, ...] | None = None) -> Decimal:
        """Decode FILQ NAV/share from its reviewed bundle schema.

        Both FILQ feeds place NAV in the second fixed-width word. FILQ-A uses
        four decimals and FILQ-D two decimals, as returned by their respective
        ``bundleDecimals()`` methods.

        :param bundle: Raw Chainlink bundle payload.
        :param bundle_decimals: Decimal layout read from the proxy at the
            bundle block. Defaults to the reviewed layout for callers that
            already validated the proxy schema.
        :return: USD NAV per FILQ share.
        """

        decimals = bundle_decimals or self.bundle_decimals
        return decode_bundle_decimal(bundle, FILQ_NAV_BUNDLE_INDEX, decimals[FILQ_NAV_BUNDLE_INDEX])

    def fetch_validated_bundle_decimals(self, block_identifier: BlockIdentifier = "latest") -> tuple[int, ...]:
        """Fetch and validate the FILQ bundle schema at a block.

        Historical callers use the schema from the same block as the bundle
        instead of assuming that the proxy's current layout applies to all
        earlier reports.

        :param block_identifier: Current or historical Ethereum block.
        :return: Reviewed decimal layout at ``block_identifier``.
        :raise ValueError: If the proxy implementation or decimals do not
            match the reviewed FILQ feed.
        """

        aggregator_address = self.price_feed_contract.functions.aggregator().call(block_identifier=block_identifier)
        if aggregator_address.lower() != FILQ_BUNDLE_AGGREGATOR_ADDRESS:
            raise ValueError(f"Unexpected FILQ bundle aggregator at block {block_identifier}: {aggregator_address}")
        bundle_decimals = tuple(self.price_feed_contract.functions.bundleDecimals().call(block_identifier=block_identifier))
        if bundle_decimals != self.bundle_decimals:
            raise ValueError(f"Unexpected FILQ bundle decimals at block {block_identifier}: {bundle_decimals}")
        return bundle_decimals

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch FILQ NAV/share from the official Chainlink bundle feed.

        :param block_identifier: Current or historical Ethereum block.
        :return: USD NAV per FILQ share.
        :raise ValueError: If the configured feed no longer matches the reviewed schema.
        """

        observation = fetch_chainlink_latest_bundle(self.web3, self.price_feed_address, block_identifier)
        if observation.aggregator_address.lower() != FILQ_BUNDLE_AGGREGATOR_ADDRESS:
            raise ValueError(f"Unexpected FILQ bundle aggregator: {observation.aggregator_address}")
        if observation.decimals != self.bundle_decimals:
            raise ValueError(f"Unexpected FILQ bundle decimals: {observation.decimals}")
        nav = observation.decode_decimal(FILQ_NAV_BUNDLE_INDEX)
        if nav <= 0:
            raise ValueError(f"FILQ bundle returned an invalid NAV: {nav}")
        return nav

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch FILQ TVL as outstanding supply multiplied by NAV/share.

        :param block_identifier: Current or historical Ethereum block.
        :return: Total USD NAV.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total FILQ NAV.

        :param block_identifier: Current or historical Ethereum block.
        :return: Total USD NAV.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> SygnumVaultInfo:
        """Return conservative FILQ scan metadata.

        :return: Token, chain and NAV-availability metadata.
        """
        return SygnumVaultInfo(token=self.address, chain_id=self.chain_id, denomination_token=None, synthetic_usd_denomination=True, nav_source="chainlink_bundle_aggregator", nav_available=True)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export explicit restrictions and price-data diagnostics.

        :return: Private scan columns.
        """
        return {
            "Denomination": "USD",
            "_denomination_token": export_sygnum_usd_denomination(self.chain_id),
            "_deposit_closed_reason": SYGNUM_RESTRICTED_FLOW_REASON,
            "_redemption_closed_reason": SYGNUM_RESTRICTED_FLOW_REASON,
            "_nav_source": "chainlink_bundle_aggregator",
            "_nav_available": True,
            "_chainlink_bundle_proxy": self.price_feed_address,
            "_chainlink_bundle_aggregator": FILQ_BUNDLE_AGGREGATOR_ADDRESS,
            "_chainlink_bundle_data_id": "0x" + self.bundle_data_id.hex(),
            "_curator_slug": self.curator_slug,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain asset portfolio for the off-chain fund.

        :param universe: Ignored trading universe.
        :param block_identifier: Ignored block identifier.
        :return: Empty spot portfolio.
        """
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public flow accounting is supported."""
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether ERC-4626 position distribution is supported."""
        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported public flow accounting.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError(SYGNUM_RESTRICTED_FLOW_REASON)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unsupported public subscription/redemption flows.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError(SYGNUM_RESTRICTED_FLOW_REASON)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return why public subscription is unavailable."""
        return SYGNUM_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return why public redemption is unavailable."""
        return SYGNUM_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a bundle-priced historical reader.

        :param stateful: Retained for reader compatibility.
        :return: FILQ supply and Chainlink bundle NAV reader.
        """
        return SygnumVaultHistoricalReader(self, stateful)

    def get_fee_data(self) -> FeeData:
        """Return unknown fee data because the token has no fee surface.

        :return: Broken fee-data sentinel.
        """
        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown product management fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return unknown product performance fee.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return unknown dealing lock-up.

        :return: ``None``.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official FILQ product page.

        :param referral: Ignored referral.
        :return: Official FILQ URL.
        """
        return "https://www.sygnum.com/filq/"
