"""Midas tokenised product vault adapter.

Midas products are not ERC-4626 or ERC-7540 vaults. Each product is an ERC-20
``mToken`` whose issuance and redemption are handled by separate Midas vault
contracts. The share price is published through Midas NAV datafeed contracts.

This adapter exposes Midas products through :class:`eth_defi.vault.base.VaultBase`
so the shared vault scanner can read historical share prices and TVL.
"""

# Adapter classes intentionally mirror :class:`VaultBase` method signatures.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.midas.constants import MIDAS_PRODUCTS
from eth_defi.midas.historical import MidasVaultHistoricalReader
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.lower_case_dict import LowercaseDict

MIDAS_HOMEPAGE = "https://midas.app/products"
MIDAS_CONTRACTS_GITHUB = "https://github.com/midas-apps/contracts"
MIDAS_DOCS = "https://docs.midas.app/"
MIDAS_REGISTRY = "https://docs.midas.app/resources/smart-contracts-registry"
MIDAS_NAV_SOURCE = "midas_data_feed_getDataInBase18"

MIDAS_BESPOKE_FLOW_REASON = "Midas issuance and redemption use bespoke product vault contracts and eligibility checks"

MIDAS_DATA_FEED_ABI = [
    {
        "inputs": [],
        "name": "getDataInBase18",
        "outputs": [{"internalType": "uint256", "name": "answer", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

MIDAS_AGGREGATOR_V3_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

MIDAS_MANAGEABLE_VAULT_ABI = [
    {
        "inputs": [],
        "name": "instantFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class MidasVaultInfo(VaultInfo, total=False):
    """Midas product metadata and compatibility settings."""

    #: ERC-20 mToken address.
    token: HexAddress

    #: Chain id.
    chain_id: int

    #: Midas ``IDataFeed`` contract used for NAV/share.
    data_feed: HexAddress

    #: Chainlink-compatible public oracle for NAV/share.
    oracle: HexAddress | None

    #: Midas issuance vault contract.
    issuance_vault: HexAddress | None

    #: Midas redemption vault contract.
    redemption_vault: HexAddress | None

    #: Whether NAV uses a synthetic denomination token in scanner output.
    synthetic_usd_denomination: bool

    #: NAV source label.
    nav_source: str

    #: Whether NAV is estimated.
    nav_estimated: bool


def convert_midas_fee_to_percent(raw_fee: int) -> Percent:
    """Convert Midas fee units to a fractional percent.

    Midas vault contracts document fees as ``1% = 100``. Because the shared
    fee model expects fractions, ``100`` becomes ``0.01``.

    :param raw_fee:
        Raw fee value from a Midas vault contract.
    :return:
        Fractional percent.
    """

    return raw_fee / 10_000


def export_midas_usd_denomination(chain_id: int) -> dict[str, object]:
    """Export synthetic USD accounting denomination metadata.

    Midas USD products publish NAV in USD units but do not expose an ERC-4626
    ``asset()`` token. The scanner can still label the denomination as USD
    without pretending that there is a depositable ERC-20 denomination token.

    :param chain_id:
        Chain id for the scan row.
    :return:
        Fresh token-like metadata dictionary for export.
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


def _checksum_or_none(address: HexAddress | None) -> HexAddress | None:
    """Convert an optional address to its checksum form.

    :param address:
        Lower-case address or ``None``.
    :return:
        Checksum address or ``None``.
    """

    if address is None:
        return None

    return HexAddress(Web3.to_checksum_address(address))


class MidasVault(VaultBase):
    """Scan-only adapter for Midas mToken products.

    The adapter reads ERC-20 supply from the mToken and NAV/share from the
    Midas datafeed contract. Active issuance and redemption are intentionally
    unsupported because Midas does not implement ERC-4626/ERC-7540 flows.
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
        """Create a Midas vault adapter.

        :param web3:
            Web3 connection.
        :param spec:
            Chain and mToken address.
        :param token_cache:
            Token metadata cache used by :py:func:`fetch_erc20_details`.
        :param features:
            Shared pipeline feature flags. Expected to contain
            :py:data:`ERC4626Feature.midas_like`.
        :param default_block_identifier:
            Default block for metadata reads.
        :param require_denomination_token:
            Whether missing denomination token should raise through
            :py:attr:`VaultBase.denomination_token`.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        self.features = features or {ERC4626Feature.midas_like}
        self.default_block_identifier = default_block_identifier

        product_key = (spec.chain_id, HexAddress(spec.vault_address.lower()))
        try:
            self.product = MIDAS_PRODUCTS[product_key]
        except KeyError as e:
            message = f"Unsupported Midas product: chain={spec.chain_id}, token={spec.vault_address}"
            raise RuntimeError(message) from e

    @property
    def chain_id(self) -> int:
        """EVM chain id for this Midas product."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Midas mToken and primary vault id."""

        return HexAddress(Web3.to_checksum_address(self.product.token))

    @property
    def vault_address(self) -> HexAddress:
        """Compatibility alias for scanner code that expects ``vault_address``."""

        return self.address

    @property
    def data_feed_contract(self) -> Contract:
        """Midas ``IDataFeed`` contract for this product."""

        return self.web3.eth.contract(
            address=Web3.to_checksum_address(self.product.data_feed),
            abi=MIDAS_DATA_FEED_ABI,
        )

    @property
    def custom_feed_contract(self) -> Contract | None:
        """Chainlink-compatible public oracle contract for this product."""

        if self.product.oracle is None:
            return None

        return self.web3.eth.contract(
            address=Web3.to_checksum_address(self.product.oracle),
            abi=MIDAS_AGGREGATOR_V3_ABI,
        )

    @property
    def issuance_vault_contract(self) -> Contract | None:
        """Midas issuance vault contract for this product."""

        if self.product.issuance_vault is None:
            return None

        return self.web3.eth.contract(
            address=Web3.to_checksum_address(self.product.issuance_vault),
            abi=MIDAS_MANAGEABLE_VAULT_ABI,
        )

    @property
    def redemption_vault_contract(self) -> Contract | None:
        """Midas redemption vault contract for this product."""

        if self.product.redemption_vault is None:
            return None

        return self.web3.eth.contract(
            address=Web3.to_checksum_address(self.product.redemption_vault),
            abi=MIDAS_MANAGEABLE_VAULT_ABI,
        )

    @property
    def name(self) -> str:
        """Token name, falling back to static product metadata."""

        token_name = self.share_token.name
        return token_name or self.product.product_name

    @property
    def symbol(self) -> str:
        """Vault share token symbol."""

        return self.share_token.symbol

    @property
    def description(self) -> str | None:
        """Human-readable product description."""

        return self.product.product_name

    @property
    def short_description(self) -> str | None:
        """Short product description."""

        return "Midas tokenised investment product with NAV published through the Midas oracle pipeline"

    @property
    def manager_name(self) -> str | None:
        """Issuer or platform display name."""

        return "Midas"

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the mToken address.

        :param block_identifier:
            Accepted for compatibility with the shared historical scanner.
        :return:
            Midas mToken address.
        """

        return self.address

    def fetch_share_token(self) -> TokenDetails:
        """Fetch ERC-20 metadata for the mToken.

        :return:
            Token details for the Midas share token.
        """

        return fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Midas share token for vault {self.address}",
        )

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return the ERC-20 denomination token address.

        Midas products use NAV feeds rather than ERC-4626 ``asset()`` tokens.
        The initial supported products are USD-denominated, represented with a
        synthetic scanner denomination.

        :return:
            Always ``None`` for the current Midas adapter.
        """

        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Fetch ERC-20 denomination token metadata.

        :return:
            Always ``None`` because Midas products do not expose an ERC-4626
            denomination token.
        """

        return None

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch Midas NAV per mToken.

        The primary source is Midas ``IDataFeed.getDataInBase18()``. Some
        registry products expose a datafeed that currently reverts as
        unhealthy or deprecated, while the paired public ``customFeed`` still
        exposes a positive Chainlink-style round answer. In that case the
        adapter falls back to ``latestRoundData()`` so historical TVL scans can
        still cover the registry-supported product.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            NAV/share in the product denomination.
        """

        try:
            raw_price = self.data_feed_contract.functions.getDataInBase18().call(block_identifier=block_identifier)
            return Decimal(raw_price) / Decimal(10**18)
        except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception):
            custom_feed = self.custom_feed_contract
            if custom_feed is None:
                raise

            _round_id, answer, _started_at, updated_at, _answered_in_round = custom_feed.functions.latestRoundData().call(block_identifier=block_identifier)
            if answer <= 0 or updated_at == 0:
                raise

            decimals = custom_feed.functions.decimals().call(block_identifier=block_identifier)
            return Decimal(answer) / Decimal(10**decimals)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch total outstanding mToken supply.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Human-readable token supply.
        """

        raw_supply = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        return self.share_token.convert_to_decimals(raw_supply)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch Midas product TVL from supply and NAV/share.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Total assets in the product denomination.
        """

        return self.fetch_total_supply(block_identifier) * self.fetch_share_price(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Fetch Midas product NAV.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Total assets in the product denomination.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> MidasVaultInfo:
        """Return Midas metadata for this scan-only adapter.

        :return:
            Token and NAV-source metadata.
        """

        return MidasVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            data_feed=Web3.to_checksum_address(self.product.data_feed),
            oracle=_checksum_or_none(self.product.oracle),
            issuance_vault=_checksum_or_none(self.product.issuance_vault),
            redemption_vault=_checksum_or_none(self.product.redemption_vault),
            denomination_token=self.fetch_denomination_token_address(),
            synthetic_usd_denomination=self.product.denomination == "USD",
            nav_source=MIDAS_NAV_SOURCE,
            nav_estimated=False,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Midas-specific scan-row diagnostics.

        :return:
            Private scan-row fields describing the NAV source and related
            Midas contracts.
        """

        return {
            "Denomination": self.product.denomination,
            "_denomination_token": export_midas_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": MIDAS_NAV_SOURCE,
            "_nav_estimated": False,
            "_synthetic_usd_denomination": self.product.denomination == "USD",
            "_midas_data_feed": Web3.to_checksum_address(self.product.data_feed),
            "_midas_oracle": _checksum_or_none(self.product.oracle),
            "_midas_issuance_vault": _checksum_or_none(self.product.issuance_vault),
            "_midas_redemption_vault": _checksum_or_none(self.product.redemption_vault),
        }

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Return an empty portfolio for the scan-only adapter.

        Midas product collateral is not held as ERC-20 balances on the mToken
        contract. Portfolio composition requires Midas transparency data and is
        out of scope for this adapter.

        :param universe:
            Ignored.
        :param block_identifier:
            Ignored.
        :return:
            Empty spot portfolio.
        """

        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Whether event-based deposit/redemption flow accounting is implemented."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Whether deposits are automatically distributed to positions."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Get flow manager.

        :raises NotImplementedError:
            Always, because Midas flow accounting is not implemented yet.
        """

        message = "Midas flow accounting is not implemented"
        raise NotImplementedError(message)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Get deposit manager.

        :raises NotImplementedError:
            Always, because active Midas issuance and redemption are not
            implemented.
        """

        message = "Midas active issuance/redemption is not implemented"
        raise NotImplementedError(message)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return the public integration deposit availability status.

        :return:
            Human-readable reason why generic deposits are closed.
        """

        return MIDAS_BESPOKE_FLOW_REASON

    def fetch_redemption_closed_reason(self) -> str | None:
        """Return the public integration redemption availability status.

        :return:
            Human-readable reason why generic redemptions are closed.
        """

        return MIDAS_BESPOKE_FLOW_REASON

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Midas historical reader.

        :param stateful:
            Whether to attach adaptive reader state.
        :return:
            Historical reader.
        """

        return MidasVaultHistoricalReader(self, stateful=stateful)

    def get_fee_data(self) -> FeeData:
        """Return Midas fee data.

        The Midas NAV process deducts product fees before publishing NAV/share.
        The exact management and performance fee split is product-specific and
        not exposed through the mToken surface, so those fields are left
        unknown. Instant issuance/redemption fees are read from the respective
        Midas vault contracts at the adapter's default block.

        :return:
            Fee data for this Midas product.
        """

        block_identifier = self.default_block_identifier or "latest"

        return FeeData(
            fee_mode=VaultFeeMode.internalised_skimming,
            management=self.get_management_fee(block_identifier),
            performance=self.get_performance_fee(block_identifier),
            deposit=self.get_deposit_fee(block_identifier),
            withdraw=self.get_withdraw_fee(block_identifier),
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return Midas management fee.

        :param block_identifier:
            Ignored. Product-level annual fee split is not exposed on-chain.
        :return:
            ``None`` because the fee split is not available from the mToken.
        """

        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return Midas performance fee.

        :param block_identifier:
            Ignored. Product-level annual fee split is not exposed on-chain.
        :return:
            ``None`` because the fee split is not available from the mToken.
        """

        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return instant issuance fee.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Instant issuance fee as a fraction, if exposed by the registry
            issuance vault.
        """

        contract = self.issuance_vault_contract
        if contract is None:
            return None

        raw_fee = contract.functions.instantFee().call(block_identifier=block_identifier)
        return convert_midas_fee_to_percent(raw_fee)

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return instant redemption fee.

        :param block_identifier:
            Historical or latest block identifier.
        :return:
            Instant redemption fee as a fraction, if exposed by the registry
            redemption vault.
        """

        contract = self.redemption_vault_contract
        if contract is None:
            return None

        raw_fee = contract.functions.instantFee().call(block_identifier=block_identifier)
        return convert_midas_fee_to_percent(raw_fee)

    def get_link(self, referral: str | None = None) -> str:
        """Get Midas product page link.

        :param referral:
            Ignored. Midas product URLs do not use referral parameters here.
        :return:
            Midas product listing URL.
        """

        return MIDAS_HOMEPAGE
