"""Franklin Templeton Benji tokenised-fund adapter.

Benji's Ethereum fund tokens are permissioned ERC-20 share contracts backed by
Franklin Templeton's transfer-agent and recordkeeping platform.  They are not
ERC-4626 vaults: public subscriptions, redemptions and transfers require
issuer authorisation and use protocol-specific servicing flows.

Authoritative contract registry:
https://digitalassets.franklintempleton.com/benji/benji-contracts/
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.franklin.constants import FRANKLIN_PRODUCTS
from eth_defi.tokenised_fund.franklin.historical import FranklinVaultHistoricalReader
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import BROKEN_FEE_DATA, FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

#: ``MoneyMarketFund_V6.lastKnownPrice()`` view ABI.
#:
#: Verified implementation source: https://etherscan.io/address/0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD#code
FRANKLIN_FUND_ABI = [
    {
        "inputs": [],
        "name": "lastKnownPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

#: Benji public lifecycle availability reason.
FRANKLIN_RESTRICTED_FLOW_REASON = "Franklin Templeton Benji subscriptions, redemptions and transfers require approved investors and transfer-agent compliance checks"


class FranklinVaultInfo(VaultInfo, total=False):
    """Franklin Benji metadata exposed to vault-scan consumers."""

    token: HexAddress
    chain_id: int
    nav_source: str
    synthetic_usd_denomination: bool


def export_franklin_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export synthetic USD accounting denomination metadata.

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


class FranklinVault(TokenisedFundVault):
    """Read-only adapter for Franklin Templeton Benji Ethereum fund shares.

    The issuer-managed ``lastKnownPrice`` is a base-18 USD reference price.
    It is read at the requested archive block together with the ERC-20 supply;
    callers must still assess its freshness against current issuer materials.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Create a Benji fund adapter.

        :param web3:
            Web3 connection to the product chain.
        :param spec:
            Chain and fund-token address.
        :param token_cache:
            ERC-20 metadata cache.
        :param features:
            Shared pipeline feature flags.
        :param default_block_identifier:
            Default metadata block retained for adapter compatibility.
        :param require_denomination_token:
            Whether a missing ERC-20 denomination token is an error.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.franklin_like}
        self.default_block_identifier = default_block_identifier
        self.product = FRANKLIN_PRODUCTS.get((spec.chain_id, HexAddress(spec.vault_address.lower())))

    @property
    def chain_id(self) -> int:
        """Return the EVM chain id.

        :return:
            Chain id from the vault specification.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the checksum fund-token address.

        :return:
            Benji ERC-20 proxy address.
        """

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible fund-token address.

        :return:
            Benji ERC-20 proxy address.
        """

        return self.address

    @cached_property
    def fund_contract(self) -> Contract:
        """Create the token proxy's stable price-view contract.

        :return:
            Contract exposing ``lastKnownPrice``.
        """

        return self.web3.eth.contract(address=self.address, abi=FRANKLIN_FUND_ABI)

    @property
    def name(self) -> str:
        """Return the registered product name.

        :return:
            On-chain name or reviewed registry fallback.
        """

        return self.share_token.name or (self.product.product_name if self.product else "Franklin Templeton Benji fund")

    @property
    def symbol(self) -> str:
        """Return the ERC-20 share-token symbol.

        :return:
            On-chain symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Return the registered fund description.

        :return:
            Product description, if the address is reviewed.
        """

        return self.product.description if self.product else "Permissioned Franklin Templeton Benji fund share."

    @property
    def short_description(self) -> str | None:
        """Return the registered compact fund description.

        :return:
            Product short description, if available.
        """

        return self.product.short_description if self.product else "Unclassified Franklin Templeton fund strategy"

    @property
    def manager_name(self) -> str | None:
        """Return the product manager.

        :return:
            Franklin Templeton.
        """

        return "Franklin Templeton"

    @property
    def curator_slug(self) -> str:
        """Return the curated manager identifier.

        :return:
            Franklin Templeton curator slug.
        """

        return "franklin-templeton"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the Benji token address.

        :param block_identifier:
            Accepted for historical-reader compatibility.
        :return:
            Fund token address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 share-token metadata.

        :return:
            Benji ERC-20 token details.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Franklin Benji fund token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no transferable denomination token.

        The verified ``MoneyMarketFund_V6`` share-token implementation exposes
        an issuer-maintained USD price but no ERC-20 asset or settlement-token
        getter. Benji servicing takes place outside this share-token contract,
        so the adapter must not substitute a stablecoin address.

        :return:
            ``None`` because the servicing flow does not expose an ERC-4626 asset.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no transferable denomination token.

        :return:
            ``None`` because issuer subscription assets are not adapter inputs.
        """

        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the issuer-maintained USD reference price.

        :param block_identifier:
            Historical or latest Ethereum block identifier.
        :return:
            USD reference price per human-readable fund share.
        """

        raw_price = self.fund_contract.functions.lastKnownPrice().call(block_identifier=block_identifier)
        return Decimal(raw_price) / Decimal(10**18)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read outstanding fund-share supply.

        :param block_identifier:
            Historical or latest Ethereum block identifier.
        :return:
            Human-readable share supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate USD TVL from supply and reference price.

        :param block_identifier:
            Historical or latest Ethereum block identifier.
        :return:
            USD-denominated supply multiplied by the reference price.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return the adapter's USD TVL estimate.

        :param block_identifier:
            Historical or latest Ethereum block identifier.
        :return:
            Fund total assets derived from the issuer reference price.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> FranklinVaultInfo:
        """Export adapter metadata.

        :return:
            Token identity and valuation-source metadata.
        """

        return FranklinVaultInfo(token=self.address, chain_id=self.chain_id, nav_source="last_known_price_usd_1e18", synthetic_usd_denomination=True)

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Export scan diagnostics for the permissioned fund.

        :return:
            USD denomination, restricted-flow and NAV-source details.
        """

        return {
            "Denomination": "USD",
            "_denomination_token": export_franklin_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": "last_known_price_usd_1e18",
            "_synthetic_usd_denomination": True,
            "_curator_slug": self.curator_slug,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no on-chain portfolio.

        :param universe:
            Ignored because fund holdings are not held by the share token.
        :param block_identifier:
            Ignored because no on-chain portfolio read is supported.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return whether flow-event accounting is implemented.

        :return:
            ``False`` because issuer servicing events are not modelled.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return whether deposits are distributed to on-chain positions.

        :return:
            ``False`` because the adapter has no on-chain portfolio.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported flow accounting.

        :return:
            Never returns.
        :raise NotImplementedError:
            Always, because servicing flows are not implemented.
        """

        message = "Franklin Benji flow accounting is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Explain why the public deposit capability is unavailable.

        :return:
            Issuer compliance restriction.
        """

        return FRANKLIN_RESTRICTED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Explain why the public redemption capability is unavailable.

        :return:
            Issuer compliance restriction.
        """

        return FRANKLIN_RESTRICTED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the Benji supply-and-price historical reader.

        :param stateful:
            Whether to retain adaptive reader state.
        :return:
            Protocol-specific historical reader.
        """

        return FranklinVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return unavailable product-level fee data.

        :return:
            Broken fee data because the token surface has no fund-fee schedule.
        """

        return BROKEN_FEE_DATA

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain management fee.

        :param block_identifier:
            Ignored because fees are not exposed by the token.
        :return:
            ``None``.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no on-chain performance fee.

        :param block_identifier:
            Ignored because fees are not exposed by the token.
        :return:
            ``None``.
        """

        return None

    def get_notes(self) -> str:
        """Return product-specific caveats.

        :return:
            Notes explaining the issuer-controlled reference price and flows.
        """

        product_name = self.product.product_name if self.product else "Unknown Franklin Benji fund"
        return f"{product_name} is a permissioned Benji share token. The adapter reads the issuer-maintained lastKnownPrice reference at each block; it does not certify investor eligibility, price freshness or public subscription/redemption availability."

    def get_link(self, referral: str | None = None) -> str:
        """Return the official Benji platform link.

        :param referral:
            Ignored because Benji does not expose a public address URL pattern.
        :return:
            Official Benji homepage.
        """

        return "https://digitalassets.franklintempleton.com/benji/"
