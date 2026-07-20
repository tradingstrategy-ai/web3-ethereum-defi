"""Reusable read-only adapter for permissioned ERC-20 fund shares.

Several fund issuers expose a verified ERC-20 share token but no public,
machine-readable NAV endpoint.  This module preserves discoverability and
outstanding supply without turning an unverified USD assumption into TVL.
"""

# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.sygnum.historical import SygnumVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict


class SupplyOnlyTokenisedFundVault(TokenisedFundVault):
    """Base for reviewed permissioned fund shares with unavailable public NAV.

    Subclasses provide a product record and their classification feature.  The
    scanner may still export identity and ERC-20 supply, but it must leave
    price and TVL empty until an issuer source is independently verified.
    """

    product: object
    feature: ERC4626Feature
    protocol_name: str
    curator: str
    homepage: str
    restricted_flow_reason: str
    nav_unavailable_reason: str

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a supply-only tokenised-fund adapter.

        :param web3: Web3 connection for the reviewed chain.
        :param spec: Chain and ERC-20 share-token address.
        :param token_cache: Optional shared token metadata cache.
        :param features: Detected hardcoded protocol feature.
        :param default_block_identifier: Optional default historical block.
        :param require_denomination_token: Retained ``VaultBase`` compatibility option.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3, self.spec, self.features = web3, spec, features or {self.feature}
        self.default_block_identifier = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the EVM chain hosting the share token."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum ERC-20 share-token address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""

        return self.address

    @property
    def name(self) -> str:
        """Return the on-chain name, falling back to reviewed metadata."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the on-chain symbol, falling back to reviewed metadata."""

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str:
        """Return the reviewed product description."""

        return self.product.description

    @property
    def short_description(self) -> str:
        """Return the compact listing description."""

        return "Permissioned tokenised fund share with supply-only public data"

    @property
    def manager_name(self) -> str:
        """Return the tokenisation platform or issuing organisation."""

        return self.curator

    @property
    def curator_slug(self) -> str:
        """Return the associated curator metadata identifier."""

        return self.curator

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ERC-20 share-token address.

        :param block_identifier: Ignored compatibility parameter.
        :return: Share-token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch verified ERC-20 share metadata.

        :return: ERC-20 token details.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"{self.protocol_name} tokenised-fund share {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no public settlement asset.

        :return: Always ``None``.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no public settlement-token metadata.

        :return: Always ``None``.
        """

        return None

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read outstanding fund shares.

        :param block_identifier: EVM block identifier.
        :return: Decimal-scaled ERC-20 total supply.
        """

        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject unverified NAV reads.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always, until a public NAV source is configured.
        """

        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject TVL calculation without a verified NAV.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always.
        """

        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject NAV calculation without a verified price source.

            :param block_identifier: Requested block identifier.
        :raise NotImplementedError: Always.
        """

        raise NotImplementedError(self.nav_unavailable_reason)

    def fetch_info(self) -> VaultInfo:
        """Export conservative token and NAV-availability metadata.

        :return: Scanner-compatible metadata mapping.
        """

        return {"token": self.address, "chain_id": self.chain_id, "denomination_token": None, "synthetic_usd_denomination": True, "nav_source": "unavailable", "nav_available": False}

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export restrictions and explicit unavailable-NAV diagnostics.

        :return: Private scanner columns.
        """

        return {"Denomination": "USD", "_deposit_closed_reason": self.restricted_flow_reason, "_redemption_closed_reason": self.restricted_flow_reason, "_nav_source": "unavailable", "_nav_available": False, "_curator_slug": self.curator_slug}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain asset portfolio.

        :param universe: Ignored trading universe.
        :param block_identifier: Ignored block identifier.
        :return: Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public fund-flow accounting is supported.

        :return: Always ``False``.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether generic ERC-4626 accounting is applicable.

        :return: Always ``False``.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported public flow accounting.

        :raise NotImplementedError: Always.
        """

        raise NotImplementedError(self.restricted_flow_reason)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject restricted public subscriptions and redemptions.

        :raise NotImplementedError: Always.
        """

        raise NotImplementedError(self.restricted_flow_reason)

    def fetch_deposit_closed_reason(self) -> str:
        """Return why public subscriptions are unavailable.

        :return: Restriction reason.
        """

        return self.restricted_flow_reason

    def fetch_redemption_closed_reason(self) -> str:
        """Return why public redemptions are unavailable.

        :return: Restriction reason.
        """

        return self.restricted_flow_reason

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create a supply-only historical reader.

        :param stateful: Whether to retain adaptive reader state.
        :return: Supply-only reader.
        """

        return SygnumVaultHistoricalReader(self, stateful)

    def get_fee_data(self) -> FeeData:
        """Return unknown fee data.

        :return: Broken fee-data sentinel.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain management-fee value.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain performance-fee value.

        :param block_identifier: Ignored block identifier.
        :return: ``None``.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the product's official homepage.

        :param referral: Ignored referral parameter.
        :return: Official product URL.
        """

        return self.homepage
