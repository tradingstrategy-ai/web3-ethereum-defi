"""Read-only adapter for reviewed Libeara tokenised fund shares.

The reviewed CUMIU and BELIF Ethereum proxies use CMTA's CMTAT framework. They
provide an issuer-maintained NAV record but their transfer rule engine and
off-chain eligibility process mean that neither public subscriptions nor
redemptions are implemented here. ULTRA on Arbitrum is tracked as supply-only
because no verified public NAV/share source has been identified.
"""

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.libeara.constants import LIBEARA_PRODUCTS
from eth_defi.tokenised_fund.libeara.historical import LibearaVaultHistoricalReader
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

CMTAT_NAV_ABI = [
    {"inputs": [], "name": "latestNAV", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "NAVScalingFactor", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "currencyNAV", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]

LIBEARA_RESTRICTED_FLOW_REASON = "Libeara fund subscriptions, redemptions and transfers require approved investors and issuer-controlled settlement"
LIBEARA_NAV_UNAVAILABLE_ERROR_PREFIX = "No verified on-chain ULTRA NAV/share source is configured"


class LibearaVaultInfo(VaultInfo, total=False):
    """Libeara fund scan metadata."""

    token: HexAddress
    chain_id: int
    nav_source: str
    synthetic_usd_denomination: bool


class LibearaVault(VaultBase):
    """Read supply and any reviewed NAV for Libeara fund shares."""

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a CMTAT fund-share adapter.

        :param web3: Web3 connection to the deployment chain.
        :param spec: Chain and reviewed fund-share address.
        :param token_cache: Optional ERC-20 metadata cache.
        :param features: Shared classification features.
        :param default_block_identifier: Default archive block for metadata reads.
        :param require_denomination_token: Retained shared-adapter compatibility flag.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3, self.spec, self.features = web3, spec, features or {ERC4626Feature.libeara_like}
        self.default_block_identifier = default_block_identifier
        self.product = LIBEARA_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address.lower())))
        if self.product is None:
            raise ValueError(f"Unsupported Libeara product: {spec.chain_id}/{spec.vault_address}")

    @property
    def chain_id(self) -> int:
        """Return the EVM chain id."""
        return self.spec.chain_id

    @property
    def is_ultra(self) -> bool:
        """Return whether this is the Arbitrum ULTRA deployment."""

        return self.product.symbol == "ULTRA"

    @property
    def address(self) -> HexAddress:
        """Return the fund-share token address."""
        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible token address."""
        return self.address

    @cached_property
    def cmtat_contract(self) -> Contract:
        """Return the stable CMTAT NAV interface.

        The ABI is sourced from the verified CUMIU proxy implementation and
        reference CMTAT contract family documented at https://github.com/CMTA/CMTAT.
        """
        return self.web3.eth.contract(address=self.address, abi=CMTAT_NAV_ABI)

    @property
    def name(self) -> str:
        """Return the reviewed product name."""
        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the ERC-20 symbol."""
        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str:
        """Return the reviewed product description."""
        return self.product.description

    @property
    def short_description(self) -> str:
        """Return the listing description."""
        return "Permissioned Libeara tokenised fund share"

    @property
    def manager_name(self) -> str:
        """Return the tokenisation platform name."""
        return "Libeara"

    @property
    def curator_slug(self) -> str:
        """Return the platform-curator feeder identifier."""
        return "libeara"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the fund share token.

        :param block_identifier: Accepted for historical reader compatibility.
        :return: Token proxy address.
        """
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 share-token metadata.

        :return: Fund-share token details.
        """
        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Libeara fund token {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no transferable denomination token.

        :return: ``None`` because the public dealing asset is not exposed.
        """
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no public denomination token.

        :return: ``None``.
        """
        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read CMTAT's issuer-maintained NAV/share.

        :param block_identifier: EVM archive block.
        :return: NAV divided by the contract-declared scale.
        :raise ValueError: If the reported scale is zero.
        """
        if self.is_ultra:
            raise NotImplementedError(f"{LIBEARA_NAV_UNAVAILABLE_ERROR_PREFIX} {self.address}")
        raw = self.cmtat_contract.functions.latestNAV().call(block_identifier=block_identifier)
        scale = self.cmtat_contract.functions.NAVScalingFactor().call(block_identifier=block_identifier)
        if not scale:
            raise ValueError("Libeara CMTAT NAVScalingFactor is zero")
        return Decimal(raw) / Decimal(scale)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read outstanding human-readable fund shares.

        :param block_identifier: Ethereum archive block.
        :return: Outstanding share supply.
        """
        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate USD value from CMTAT NAV and supply.

        :param block_identifier: Ethereum archive block.
        :return: NAV multiplied by outstanding shares.
        """
        return self.fetch_share_price(block_identifier) * self.fetch_total_supply(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return NAV-derived total assets.

        :param block_identifier: Ethereum archive block.
        :return: USD value estimate.
        """
        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> LibearaVaultInfo:
        """Export integration metadata.

        :return: Token identity and issuer-NAV source information.
        """
        nav_source = "unconfigured_external_ultra_manager" if self.is_ultra else "cmtat_latest_nav"
        return LibearaVaultInfo(token=self.address, chain_id=self.chain_id, nav_source=nav_source, synthetic_usd_denomination=not self.is_ultra)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export restricted-flow and valuation diagnostics.

        :return: Data compatible with the vault scanner.
        """
        nav_source = "unconfigured_external_ultra_manager" if self.is_ultra else "cmtat_latest_nav"
        return {"Denomination": None if self.is_ultra else "USD", "_notes": self.get_notes(), "_deposit_closed_reason": self.fetch_deposit_closed_reason(), "_redemption_closed_reason": self.fetch_redemption_closed_reason(), "_nav_source": nav_source, "_nav_estimated": False, "_curator_slug": self.curator_slug}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no token-held on-chain portfolio.

        :param universe: Ignored.
        :param block_identifier: Ignored.
        :return: Empty spot portfolio.
        """
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether servicing-flow event accounting is implemented.

        :return: Always ``False``.
        """
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits reach an on-chain portfolio.

        :return: Always ``False``.
        """
        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unimplemented issuer servicing flows.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError("Libeara fund flow accounting is not implemented")

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject public dealing operations.

        :raise NotImplementedError: Always.
        """
        raise NotImplementedError("Libeara fund subscriptions and redemptions are not implemented")

    def fetch_deposit_closed_reason(self) -> str:
        """Explain unavailable subscriptions.

        :return: Compliance restriction description.
        """
        return LIBEARA_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain unavailable redemptions.

        :return: Compliance restriction description.
        """
        return LIBEARA_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the product-specific supply and valuation reader.

        :param stateful: Retained shared-reader API parameter.
        :return: Libeara historical reader.
        """
        return LibearaVaultHistoricalReader(self)

    def get_fee_data(self) -> FeeData:
        """Return unavailable product fee data.

        :return: Broken fee data placeholder.
        """
        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain management fee.

        :param block_identifier: Ignored.
        :return: ``None``.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain performance fee.

        :param block_identifier: Ignored.
        :return: ``None``.
        """
        return None

    def get_notes(self) -> str:
        """Describe the integration boundary.

        :return: Valuation and transfer-compliance caveat.
        """
        if self.is_ultra:
            return f"{self.product.product_name} is a permissioned fund unit. The adapter does not fabricate NAV and does not certify investor eligibility or public dealing availability."
        return f"{self.product.product_name} uses a permissioned CMTAT share token. The adapter reads issuer-maintained NAV and does not certify price freshness, investor eligibility or public dealing availability."

    def get_link(self, referral: str | None = None) -> str:
        """Return Libeara's platform URL.

        :param referral: Ignored.
        :return: Official platform homepage.
        """
        if self.is_ultra:
            return "https://libeara.com/libeara-partners-with-wellington-and-fundbridge-capital-to-launch-a-u-s-treasuries-fund-tokenised-on-public-blockchain/"
        return "https://libeara.com/"
