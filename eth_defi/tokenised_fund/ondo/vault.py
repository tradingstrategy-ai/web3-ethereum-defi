"""Read-only Ondo tokenised-fund adapter.

Ondo USDY and OUSG are permissioned ERC-20 share tokens, not ERC-4626
vaults. Their published NAV is supplied by separate issuer oracles.
"""

# Adapter methods intentionally mirror :class:`VaultBase` signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.ondo.constants import ONDO_PRODUCTS, OndoProduct
from eth_defi.tokenised_fund.ondo.historical import OndoVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

ONDO_RESTRICTED_FLOW_REASON = "Ondo subscriptions, redemptions and token transfers require issuer onboarding, eligibility and compliance checks"
ONDO_USD_NAV_DECIMALS = 18

ONDO_DYNAMIC_ORACLE_ABI = [{"inputs": [], "name": "getPrice", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
ONDO_UNIFIED_ORACLE_ABI = [{"inputs": [{"name": "token", "type": "address"}], "name": "getAssetPrice", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]


class OndoVaultInfo(VaultInfo, total=False):
    """Ondo product and valuation metadata."""

    token: HexAddress
    chain_id: int
    oracle: HexAddress
    nav_source: str
    synthetic_usd_denomination: bool


def export_ondo_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export Ondo's non-transferable USD accounting denomination.

    :param chain_id:
        EVM chain id of the fund token.
    :return:
        Token-like USD metadata without an ERC-20 address.
    """

    return {
        "address": None,
        "chain": chain_id,
        "name": "United States Dollar",
        "symbol": "USD",
        "decimals": None,
        "total_supply": None,
        "extra_data": {"synthetic": True},
    }


class OndoVault(TokenisedFundVault):
    """Read token supply and official on-chain NAV for Ondo funds."""

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create an Ondo tokenised-fund adapter.

        :param web3: Ethereum connection.
        :param spec: Chain and registered Ondo share-token address.
        :param token_cache: Optional ERC-20 metadata cache.
        :param features: Shared classification features.
        :param default_block_identifier: Optional default archive block.
        :param require_denomination_token: Retained for shared adapter compatibility.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.ondo_like}
        self.default_block_identifier = default_block_identifier
        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: OndoProduct = ONDO_PRODUCTS[key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Ondo product: chain={spec.chain_id}, token={spec.vault_address}") from error

    @property
    def chain_id(self) -> int:
        """Return the registered EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum share-token address."""

        return HexAddress(Web3.to_checksum_address(self.product.token))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token address."""

        return self.address

    @property
    def oracle_contract(self) -> Contract:
        """Return the product's issuer-published NAV oracle contract."""

        abi = ONDO_DYNAMIC_ORACLE_ABI if self.product.oracle_method == "getPrice" else ONDO_UNIFIED_ORACLE_ABI
        return self.web3.eth.contract(address=Web3.to_checksum_address(self.product.oracle), abi=abi)

    @property
    def name(self) -> str:
        """Return the on-chain token name with registered fallback."""

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return the on-chain token symbol with registered fallback."""

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str:
        """Return the reviewed product description."""

        return self.product.description

    @property
    def short_description(self) -> str:
        """Return a compact product description."""

        return self.product.short_description

    @property
    def manager_name(self) -> str:
        """Return the protocol-managed fund curator name."""

        return "Ondo Finance"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the ERC-20 share-token address."""

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch token metadata for the registered ERC-20 share token."""

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Ondo tokenised fund share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no single ERC-20 denomination token for issuer fund NAV.

        Ondo accepts multiple settlement routes, currently including USDC,
        PYUSD, RLUSD and USD bank wire. None of these is the unique asset that
        denominates the issuer's USD NAV, so selecting one would be misleading.

        See `Ondo minting and redemption documentation
        <https://docs.ondo.finance/qualified-access-products/minting-and-redeeming>`__.

        :return:
            Always ``None`` because the products have USD accounting NAV.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no generic ERC-20 denomination token."""

        return None

    def fetch_oracle_call(self) -> ContractFunction:
        """Construct the canonical NAV call for the registered product."""

        if self.product.oracle_method == "getPrice":
            return self.oracle_contract.functions.getPrice()
        return self.oracle_contract.functions.getAssetPrice(self.address)

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch official USD NAV/share from the configured issuer oracle."""

        raw_price = self.fetch_oracle_call().call(block_identifier=block_identifier)
        return Decimal(raw_price) / Decimal(10**ONDO_USD_NAV_DECIMALS)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch outstanding share-token supply."""

        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate USD NAV from share supply and issuer NAV/share."""

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return USD NAV for the tokenised fund."""

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> OndoVaultInfo:
        """Export product and NAV-source metadata."""

        return OndoVaultInfo(token=self.address, chain_id=self.chain_id, oracle=self.product.oracle, nav_source=f"ondo_{self.product.oracle_method}", synthetic_usd_denomination=True)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export scanner metadata for permissioning and NAV provenance."""

        return {
            "Denomination": "USD",
            "_denomination_token": export_ondo_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": f"ondo_{self.product.oracle_method}",
            "_nav_estimated": False,
            "_synthetic_usd_denomination": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return an empty portfolio because fund holdings are not on-chain vault positions."""

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return ``False`` because public fund-flow accounting is unsupported."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return ``False`` because portfolio holdings are issuer-managed."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting."""

        message = "Ondo tokenised-fund flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject public transaction management for permissioned issuer flows."""

        message = "Ondo subscriptions and redemptions are not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why generic public subscriptions are unavailable."""

        return ONDO_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why generic public redemptions are unavailable."""

        return ONDO_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Create the product's supply-and-NAV historical reader."""

        return OndoVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return documented fund management fee where available."""

        return FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=self.product.management_fee, performance=None, deposit=None, withdraw=None)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the documented annual fund management fee, if published."""

        return self.product.management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no documented performance fee."""

        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return no fixed lock-up because eligibility and terms are product-specific."""

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official product page."""

        return self.product.homepage
