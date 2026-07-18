"""Read-only Theo iToken tokenised-fund adapter.

Theo's `iToken technical reference <https://docs.theo.xyz/technical-reference/ttokens-and-itokens>`__
defines a multi-asset basket interface. thBILL's canonical Ethereum deployment
is therefore not a conventional single-asset ERC-4626 vault. This adapter
tracks only explicit ERC-20 share supply and never converts basket shares to an
invented USD NAV.
"""

# Adapter methods intentionally mirror :class:`VaultBase` signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.theo.constants import THEO_ITOKEN_PRODUCTS, TheoITokenProduct
from eth_defi.tokenised_fund.theo.historical import TheoITokenHistoricalReader
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

#: Direct dealing is restricted to KYC-cleared participants.
THEO_ITOKEN_RESTRICTED_FLOW_REASON = "Theo thBILL minting and redemption require KYC approval and issuer-operated settlement; no public dealing flow is certified"

#: Price is intentionally unavailable until the whole iToken basket has a
#: reviewed valuation source at each historical block.
THEO_ITOKEN_NAV_UNAVAILABLE = "Theo thBILL iToken has no reviewed scalar NAV/share source; basket valuation is not configured"


class TheoITokenVaultInfo(VaultInfo, total=False):
    """Theo iToken metadata exported to scanner consumers."""

    token: HexAddress
    chain_id: int
    nav_source: str


class TheoITokenVault(VaultBase):
    """Read-only adapter for the canonical Ethereum thBILL iToken.

    This adapter deliberately leaves its denomination and price unset. The
    documented iToken accounting operates over arrays of basket assets, while
    the public direct mint/redeem lifecycle is KYC-restricted.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create an address-scoped thBILL adapter.

        :param web3: EVM JSON-RPC connection.
        :param spec: Chain and thBILL token address.
        :param token_cache: Shared ERC-20 metadata cache.
        :param features: Shared classification flags.
        :param default_block_identifier: Retained for factory compatibility.
        :param require_denomination_token: Retained for :class:`VaultBase` compatibility.
        :raise RuntimeError: If the address is not a reviewed Theo iToken.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: TheoITokenProduct = THEO_ITOKEN_PRODUCTS[key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Theo iToken: chain={spec.chain_id}, token={spec.vault_address}") from error
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.theo_itoken_like}
        self.default_block_identifier = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the canonical iToken chain id.

        :return: Ethereum mainnet id.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the iToken address.

        :return: Checksummed thBILL token address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible share-token identifier.

        :return: thBILL token address.
        """

        return self.address

    @property
    def name(self) -> str:
        """Return the reviewed product name.

        :return: On-chain name, with registry fallback.
        """

        return self.share_token.name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Return thBILL's symbol.

        :return: On-chain symbol, with registry fallback.
        """

        return self.share_token.symbol or self.product.symbol

    @property
    def description(self) -> str:
        """Return the public product description.

        :return: Product summary.
        """

        return "Basket of institutional-grade tokenised U.S. Treasury bills"

    @property
    def short_description(self) -> str:
        """Return a concise eligibility-aware description.

        :return: Compact product summary.
        """

        return "KYC-restricted multi-asset tokenised U.S. Treasury-bill basket"

    @property
    def manager_name(self) -> str:
        """Return the protocol-operated curator name.

        :return: Theo.
        """

        return "Theo"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the thBILL token address.

        :param block_identifier: Accepted for scanner compatibility.
        :return: thBILL address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 metadata for thBILL.

        :return: ERC-20 details for the canonical iToken.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Theo thBILL share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no surrogate denomination token.

        :return: Always ``None`` because iToken accounting uses a basket.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no scalar denomination token.

        :return: Always ``None``.
        """

        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject a synthetic scalar NAV.

        :param block_identifier: Requested historical block.
        :raises NotImplementedError: Always, until basket valuation is reviewed.
        """

        raise NotImplementedError(THEO_ITOKEN_NAV_UNAVAILABLE)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch human-readable thBILL supply.

        :param block_identifier: Ethereum block tag or number.
        :return: Outstanding thBILL supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject TVL calculation from supply alone.

        :param block_identifier: Requested historical block.
        :raises NotImplementedError: Always, because supply is not NAV.
        """

        raise NotImplementedError(THEO_ITOKEN_NAV_UNAVAILABLE)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Reject unreviewed basket valuation.

        :param block_identifier: Requested historical block.
        :raises NotImplementedError: Always.
        """

        raise NotImplementedError(THEO_ITOKEN_NAV_UNAVAILABLE)

    def fetch_info(self) -> TheoITokenVaultInfo:
        """Return explicit unpriced-product metadata.

        :return: thBILL product metadata.
        """

        return TheoITokenVaultInfo(token=self.address, chain_id=self.chain_id, nav_source="unconfigured_theo_itoken_basket")

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return restricted-flow and valuation diagnostics.

        :return: Scan record extra data without a synthetic denomination.
        """

        return {"Denomination": None, "_denomination_token": None, "_notes": self.get_notes(), "_deposit_closed_reason": self.fetch_deposit_closed_reason(), "_redemption_closed_reason": self.fetch_redemption_closed_reason(), "_nav_source": "unconfigured_theo_itoken_basket", "_nav_estimated": False, "_synthetic_usd_denomination": False}

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no inferred ERC-20 portfolio.

        :param universe: Ignored; basket assets are not a token balance list.
        :param block_identifier: Ignored.
        :return: Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether public flow accounting is implemented.

        :return: ``False``.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether public deposits are distributed by this adapter.

        :return: ``False``.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unimplemented issuer-operated flow accounting.

        :raises NotImplementedError: Always.
        """

        message = "Theo thBILL flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject public dealing for this KYC-restricted product.

        :raises NotImplementedError: Always.
        """

        raise NotImplementedError(THEO_ITOKEN_RESTRICTED_FLOW_REASON)

    def fetch_deposit_closed_reason(self) -> str:
        """Explain why public deposits are unavailable.

        :return: Restricted-flow reason.
        """

        return THEO_ITOKEN_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain why public redemptions are unavailable.

        :return: Restricted-flow reason.
        """

        return THEO_ITOKEN_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the supply-only historical reader.

        :param stateful: Requested scanner reader-state mode.
        :return: Theo iToken reader.
        """

        return TheoITokenHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return unavailable fee data.

        :return: Broken fee data because no official thBILL fee schedule was found.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the unavailable management fee.

        :param block_identifier: Ignored.
        :return: ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the unavailable performance fee.

        :param block_identifier: Ignored.
        :return: ``None``.
        """

        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the official thBILL product page.

        :param referral: Ignored; Theo does not publish a referral URL.
        :return: Official product documentation URL.
        """

        return self.product.homepage
