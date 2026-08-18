"""Spiko permissioned tokenised-fund adapter.

Spiko fund shares are permissioned ERC-20 tokens, not ERC-4626 vaults. The
issuer's verified Oracle contract publishes NAV/share using the Chainlink
AggregatorV3 interface. Combining that NAV with ERC-20 supply gives a safe
read-only estimate of the tokenised fund's total NAV.

See https://tech.spiko.io/posts/spiko-smart-contracts/ and
https://github.com/spiko-tech/contracts/blob/main/contracts/oracle/Oracle.sol.
"""

#: Adapter methods intentionally mirror :class:`VaultBase` signatures.
# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.tokenised_fund.spiko.constants import SPIKO_PRODUCTS, SpikoProduct
from eth_defi.tokenised_fund.spiko.constants import USTBL_MANAGEMENT_FEE as _USTBL_MANAGEMENT_FEE
from eth_defi.tokenised_fund.spiko.historical import SpikoHistoricalReader
from eth_defi.tokenised_fund.spiko.tags import STRATEGY_TAGS
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Public flows must not be advertised for Spiko's permissioned lifecycle.
SPIKO_PERMISSIONED_FLOW_REASON = "Spiko subscriptions, transfers and redemptions require eligibility checks and issuer-operated daily servicing"

#: Backwards-compatible USTBL fee constant exported by this adapter module.
USTBL_MANAGEMENT_FEE = _USTBL_MANAGEMENT_FEE

_ORACLE_ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [{"type": "uint80"}, {"type": "int256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint80"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]


class SpikoVaultInfo(VaultInfo, total=False):
    """Spiko product metadata exported to vault scan consumers."""

    token: HexAddress
    chain_id: int
    price_oracle: HexAddress
    usd_price_oracle: HexAddress | None
    nav_source: str
    source_denomination: str
    synthetic_denomination: bool
    synthetic_usd_denomination: bool


def export_spiko_denomination(chain_id: int, symbol: str) -> dict[str, object]:
    """Export a non-transferable currency-denomination record.

    Spiko fund subscriptions can settle through issuer-operated routes, but the
    fund-token contract does not expose an ERC-4626 ``asset`` token. This
    accounting-only record makes the issuer NAV currency explicit without
    advertising a public ERC-20 dealing route.

    :param chain_id:
        EVM chain id of the Spiko product.
    :param symbol:
        ISO currency symbol used by the issuer NAV oracle.
    :return:
        Token-like non-transferable currency metadata.
    """

    currency_names = {"EUR": "Euro", "USD": "United States Dollar"}
    return {
        "address": None,
        "chain": chain_id,
        "name": currency_names.get(symbol, symbol),
        "symbol": symbol,
        "decimals": None,
        "total_supply": None,
        "extra_data": {"synthetic": True},
    }


def export_spiko_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export USTBL's legacy USD accounting-denomination record.

    :param chain_id:
        EVM chain id of the USTBL deployment.
    :return:
        Token-like USD metadata without an ERC-20 address.
    """

    return export_spiko_denomination(chain_id, "USD")


class SpikoVault(TokenisedFundVault):
    """Read-only adapter for reviewed Spiko tokenised-fund shares.

    The adapter calculates NAV from ERC-20 supply and the official issuer
    Oracle. It cannot perform investor dealing: transfers and servicing are
    controlled by Spiko's permission manager and redemption workflow.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, token_cache: dict | None = None, features: set[ERC4626Feature] | None = None, default_block_identifier: BlockIdentifier | None = None, require_denomination_token: bool = False):
        """Create a verified Spiko product adapter.

        :param web3:
            Web3 connection to the product's EVM chain.
        :param spec:
            Chain and reviewed Spiko share-token identifier.
        :param token_cache:
            Optional ERC-20 metadata cache.
        :param features:
            Classification flags supplied by the shared factory.
        :param default_block_identifier:
            Accepted for factory compatibility.
        :param require_denomination_token:
            Accepted for :class:`VaultBase` compatibility.
        :raise ValueError:
            If the requested product has not been reviewed.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product: SpikoProduct = SPIKO_PRODUCTS[key]
        except KeyError as e:
            raise ValueError(f"Unsupported Spiko product: chain={spec.chain_id}, token={spec.vault_address}") from e
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.spiko_like}
        self.first_seen_at_block = self.product.first_seen_at_block
        self.oracle_first_seen_at_block = self.product.oracle_first_seen_at_block
        _ = default_block_identifier

    @property
    def chain_id(self) -> int:
        """Return the reviewed Spiko deployment chain id.

        :return:
            EVM chain id of this product.
        """

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the Spiko share-token address.

        :return:
            Checksummed ERC-20 address.
        """

        return HexAddress(Web3.to_checksum_address(self.product.token))

    @property
    def vault_address(self) -> HexAddress:
        """Return the shared scanner vault identifier.

        :return:
            Spiko share-token address.
        """

        return self.address

    @cached_property
    def price_oracle_contract(self) -> Contract:
        """Return Spiko's verified Chainlink-compatible NAV oracle.

        :return:
            Oracle contract instance.
        """

        return self.web3.eth.contract(address=Web3.to_checksum_address(self.product.price_oracle), abi=_ORACLE_ABI)

    @cached_property
    def usd_price_oracle_contract(self) -> Contract | None:
        """Return the optional issuer-currency to USD oracle.

        :return:
            Chainlink-compatible FX oracle for non-USD products, if reviewed.
        """

        if self.product.usd_price_oracle is None:
            return None
        return self.web3.eth.contract(address=Web3.to_checksum_address(self.product.usd_price_oracle), abi=_ORACLE_ABI)

    @cached_property
    def oracle_decimals(self) -> int:
        """Read the NAV oracle decimal scale.

        :return:
            Number of oracle decimal places.
        """

        return self.price_oracle_contract.functions.decimals().call()

    @cached_property
    def usd_price_oracle_decimals(self) -> int | None:
        """Read the USD FX oracle decimal scale when configured.

        :return:
            FX oracle decimal places, or ``None`` for USD-native products.
        """

        if self.usd_price_oracle_contract is None:
            return None
        return self.usd_price_oracle_contract.functions.decimals().call()

    @property
    def name(self) -> str:
        """Return the onchain share-token name.

        :return:
            ERC-20 token name.
        """

        return self.share_token.name

    @property
    def symbol(self) -> str:
        """Return the onchain share-token symbol.

        :return:
            ERC-20 token symbol.
        """

        return self.share_token.symbol

    @property
    def description(self) -> str:
        """Return the public fund description.

        :return:
            Product-specific investment strategy description.
        """

        return self.product.description

    @property
    def short_description(self) -> str:
        """Return a concise public listing description.

        :return:
            Product-specific strategy summary.
        """

        return self.product.short_description

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Spiko fund.

        :return:
            Copy of the tag set, or ``None`` when this fund has not yet been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    @property
    def manager_name(self) -> str:
        """Return the protocol-operated curator identity.

        :return:
            Spiko.
        """

        return "Spiko"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the Spiko share-token address.

        :param block_identifier:
            Accepted for shared scanner compatibility.
        :return:
            Spiko ERC-20 share-token address.
        """

        _ = block_identifier
        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch Spiko ERC-20 token details.

        :return:
            Share-token metadata and conversion methods.
        """

        return fetch_erc20_details(self.web3, self.address, chain_id=self.chain_id, raise_on_error=False, cache=self.token_cache, cause_diagnostics_message=f"Spiko {self.product.symbol} share token for vault {self.address}")

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return no surrogate ERC-20 denomination token.

        :return:
            Always ``None`` because no single public ERC-20 asset exists.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Return no onchain denomination-token metadata.

        :return:
            Always ``None`` because Spiko's issuer NAV currency is not a public
            ERC-20 subscription asset.
        """

        return None

    def convert_raw_share_price(self, raw_price: int) -> Decimal:
        """Convert oracle units to the issuer's NAV currency per token.

        :param raw_price:
            Raw Chainlink-compatible oracle answer.
        :return:
            Human-readable NAV/share in the product denomination.
        """

        return Decimal(raw_price) / Decimal(10**self.oracle_decimals)

    def convert_raw_usd_exchange_rate(self, raw_price: int) -> Decimal:
        """Convert FX-oracle units to USD per issuer currency unit.

        :param raw_price:
            Raw answer from the reviewed Chainlink FX oracle.
        :return:
            USD per one source-denomination unit.
        """

        assert self.usd_price_oracle_decimals is not None, "No USD FX oracle configured"
        return Decimal(raw_price) / Decimal(10**self.usd_price_oracle_decimals)

    def convert_source_share_price_to_usd(self, source_share_price: Decimal, usd_exchange_rate: Decimal | None = None, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Convert an issuer NAV/share to the shared USD denomination.

        :param source_share_price:
            NAV/share in Spiko's published source currency.
        :param usd_exchange_rate:
            Optional USD per source-currency unit. When omitted, read the
            reviewed FX oracle at ``block_identifier``.
        :param block_identifier:
            EVM block tag for the optional FX oracle read.
        :return:
            USD-normalised NAV/share.
        """

        if self.usd_price_oracle_contract is None:
            return source_share_price
        if usd_exchange_rate is None:
            _round, answer, _started, updated_at, _answered = self.usd_price_oracle_contract.functions.latestRoundData().call(block_identifier=block_identifier)
            if answer <= 0 or updated_at <= 0:
                raise ValueError(f"Spiko {self.product.symbol} USD FX oracle returned an invalid observation")
            usd_exchange_rate = self.convert_raw_usd_exchange_rate(answer)
        return source_share_price * usd_exchange_rate

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the official NAV/share.

        :param block_identifier:
            EVM block tag or historical block number.
        :return:
            NAV/share in USD accounting units.
        :raise ValueError:
            If the oracle returns no valid NAV observation.
        """

        _round, answer, _started, updated_at, _answered = self.price_oracle_contract.functions.latestRoundData().call(block_identifier=block_identifier)
        if answer <= 0 or updated_at <= 0:
            raise ValueError(f"Spiko {self.product.symbol} oracle returned an invalid NAV observation")
        return self.convert_source_share_price_to_usd(self.convert_raw_share_price(answer), block_identifier=block_identifier)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the outstanding share supply.

        :param block_identifier:
            EVM block tag or historical block number.
        :return:
            Human-readable outstanding shares.
        """

        return self.share_token.convert_to_decimals(self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier))

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate estimated fund NAV from supply and issuer NAV/share.

        :param block_identifier:
            EVM block tag or historical block number.
        :return:
            Estimated total fund NAV in USD accounting units.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the tokenised fund's total NAV.

        :param block_identifier:
            EVM block tag or historical block number.
        :return:
            Estimated total fund NAV in USD accounting units.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> SpikoVaultInfo:
        """Export verified Spiko integration metadata.

        :return:
            Token, oracle, denomination and NAV-source identifiers.
        """

        return SpikoVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            price_oracle=HexAddress(Web3.to_checksum_address(self.product.price_oracle)),
            usd_price_oracle=HexAddress(Web3.to_checksum_address(self.product.usd_price_oracle)) if self.product.usd_price_oracle else None,
            nav_source=self.product.nav_source,
            source_denomination=self.product.denomination_symbol,
            synthetic_denomination=True,
            synthetic_usd_denomination=True,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Expose valuation and restricted-flow diagnostics.

        :return:
            Product-specific scanner metadata.
        """

        return {
            "Denomination": "USD",
            "_denomination_token": export_spiko_denomination(self.chain_id, "USD"),
            "_nav_source": self.product.nav_source,
            "_nav_estimated": False,
            "_spiko_price_oracle": HexAddress(Web3.to_checksum_address(self.product.price_oracle)),
            "_spiko_source_denomination": self.product.denomination_symbol,
            "_source_denomination": self.product.denomination_symbol,
            "_synthetic_denomination": True,
            "_synthetic_usd_denomination": True,
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return no directly observable underlying portfolio.

        :param universe:
            Ignored because holdings are offchain.
        :param block_identifier:
            Ignored because holdings are offchain.
        :return:
            Empty spot portfolio.
        """

        _ = universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Report unsupported generic flow-event accounting.

        :return:
            ``False`` because servicing is issuer-operated.
        """

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Report unavailable onchain portfolio distribution.

        :return:
            ``False``.
        """

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Reject unsupported generic flow accounting.

        :raise NotImplementedError:
            Always, as Spiko servicing is bespoke.
        """

        raise NotImplementedError(f"Spiko {self.product.symbol} subscription and redemption flow accounting is not implemented")

    def fetch_deposit_closed_reason(self) -> str:
        """Explain unavailable generic subscriptions.

        :return:
            Eligibility and issuer-servicing explanation.
        """

        return SPIKO_PERMISSIONED_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str:
        """Explain unavailable generic redemptions.

        :return:
            Eligibility and issuer-servicing explanation.
        """

        return SPIKO_PERMISSIONED_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Construct the supply and NAV historical reader.

        :param stateful:
            Whether to retain shared adaptive reader state.
        :return:
            Spiko historical reader.
        """

        return SpikoHistoricalReader(self, stateful=stateful)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the published annual management fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            Annual management fee as a fraction.
        """

        _ = block_identifier
        return self.product.management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return no separately published performance fee.

        :param block_identifier:
            Accepted for shared fee API compatibility.
        :return:
            ``None``.
        """

        _ = block_identifier
        return None

    def get_fee_data(self) -> FeeData:
        """Return published Spiko fee metadata.

        :return:
            Management fee with no inferred dealing fees.
        """

        return FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=self.product.management_fee, performance=None, deposit=None, withdraw=None)

    def get_link(self, referral: str | None = None) -> str:
        """Return the official Spiko product page.

        :param referral:
            Ignored because Spiko does not provide referral URLs.
        :return:
            Official product page.
        """

        _ = referral
        return self.product.product_url
