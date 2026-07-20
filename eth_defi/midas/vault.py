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

from eth_defi.abi import ZERO_ADDRESS
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.midas.constants import MIDAS_PRODUCTS
from eth_defi.midas.historical import MidasVaultHistoricalReader
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultDepositManager, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
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
        "name": "getPaymentTokens",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
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

    #: Whether the scanner used the off-chain USD fallback because no payment
    #: token was available.
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
    """Export the off-chain USD fallback denomination metadata.

    This is used only when a Midas issuance vault has no configured ERC-20
    payment token. It records the product's USD accounting label without
    inventing an ERC-20 address.

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
    """Scan-only adapter for Midas ``mToken`` investment products.

    Midas product architecture differs materially from ERC-4626. The mToken
    is the share token, while separate issuance and redemption vault contracts
    perform deposits and withdrawals. NAV/share is published by Midas'
    ``IDataFeed`` and is independent of any ERC-20 used to pay for an
    issuance. Consequently this adapter must not call ERC-4626 ``asset()`` or
    imply that a payment token controls the mToken's NAV denomination.

    Instead, an issuance vault inherits Midas'
    `ManageableVault <https://github.com/midas-apps/contracts/blob/main/contracts/abstract/ManageableVault.sol>`__
    implementation. It maintains a mutable ``_paymentTokens`` set and exposes
    it through ``getPaymentTokens()``; Midas administrators can add or remove
    accepted payment tokens. :py:meth:`fetch_payment_tokens` reads this
    authoritative live list. Since the shared vault schema presently has one
    ERC-20 denomination field, the adapter exports the first returned payment
    token as a compatibility choice. That order is not an assertion that Midas
    regards it as a canonical or preferred settlement currency.

    If an issuance vault is absent, returns no ERC-20 payment tokens, or only
    returns the zero-address ``MANUAL_FULLFILMENT_TOKEN`` sentinel, the adapter
    exports synthetic off-chain USD. This preserves the USD accounting label
    without inventing an ERC-20 address. Active issuance and redemption remain
    intentionally unsupported because Midas does not implement
    ERC-4626/ERC-7540 flows.
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

        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        if metadata:
            return metadata.description
        return self.product.product_name

    @property
    def short_description(self) -> str | None:
        """Short product description."""

        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        if metadata:
            return metadata.short_description
        return "Midas tokenised investment product with NAV published through the Midas oracle pipeline"

    @property
    def manager_name(self) -> str | None:
        """Issuer or platform display name."""

        return "Midas"

    def get_flags(self) -> set[VaultFlag]:
        """Return the product-specific vault classification flags.

        Midas serves both regulated tokenised funds and crypto strategy
        products through the same contract family.  Only reviewed ``mTBILL``
        product records receive the tokenised-fund listing flag.

        :return: Generic flags, with ``tokenised_fund`` for mTBILL only.
        """

        flags = set(super().get_flags())
        if self.product.is_tokenised_fund:
            flags.add(VaultFlag.tokenised_fund)
        return flags

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

    def fetch_payment_tokens(self) -> list[TokenDetails]:
        """Fetch ERC-20 payment tokens accepted by the Midas issuance vault.

        Midas products have no ERC-4626 ``asset()`` method. Their separate
        issuance contracts inherit
        `ManageableVault <https://github.com/midas-apps/contracts/blob/main/contracts/abstract/ManageableVault.sol>`__,
        whose ``getPaymentTokens()`` method returns its internal
        ``_paymentTokens`` set. This is the authoritative on-chain list of
        ERC-20 tokens accepted to pay for a deposit. The companion
        `DepositVault implementation <https://github.com/midas-apps/contracts/blob/main/contracts/DepositVault.sol>`__
        converts the supplied ``tokenIn`` amount to USD before calculating the
        mToken mint amount. Therefore a returned payment token is a settlement
        route, not necessarily the currency used by the product's NAV feed.

        The set is mutable: Midas vault administrators may add and remove
        payment tokens, so this method intentionally performs a live read and
        does not cache the returned list. The contract uses OpenZeppelin's
        enumerable set, whose returned order is an implementation order rather
        than an explicit Midas preference. We preserve it exactly because
        :py:meth:`fetch_primary_payment_token` must choose one token for the
        shared single-denomination schema; it selects the first item solely as
        that compatibility rule.

        Midas also defines ``MANUAL_FULLFILMENT_TOKEN`` as the zero address for
        an off-chain USD bank-transfer route. Current supported products return
        ERC-20 addresses, but this method filters a zero-address entry so it
        cannot be mistaken for a transferable ERC-20. When there is no
        issuance vault, there are no ERC-20 entries, or the returned list only
        has the manual-fulfilment sentinel, this method returns an empty list.
        The scan export then falls back to synthetic off-chain USD instead of
        inventing a token address.

        :return:
            Payment-token details in the contract-returned order, or an empty
            list when the product has no issuance vault or configured ERC-20
            payment tokens.
        """

        issuance_vault = self.issuance_vault_contract
        if issuance_vault is None:
            return []

        block_identifier = self.default_block_identifier or "latest"
        payment_token_addresses = issuance_vault.functions.getPaymentTokens().call(block_identifier=block_identifier)
        return [
            fetch_erc20_details(
                self.web3,
                Web3.to_checksum_address(address),
                chain_id=self.chain_id,
                raise_on_error=False,
                cache=self.token_cache,
                cause_diagnostics_message=f"Midas payment token for vault {self.address}",
            )
            for address in payment_token_addresses
            if address.lower() != ZERO_ADDRESS
        ]

    def fetch_primary_payment_token(self) -> TokenDetails | None:
        """Fetch the primary Midas payment token for schema compatibility.

        Midas exposes a set of accepted payment tokens, rather than an
        ERC-4626 ``asset()`` token. The shared :class:`VaultBase` API has one
        denomination-token slot, however, so Midas adapters define the first
        ERC-20 returned by
        `ManageableVault.getPaymentTokens() <https://github.com/midas-apps/contracts/blob/main/contracts/abstract/ManageableVault.sol>`__
        as the primary payment token. This is a compatibility convention, not
        a claim that the token denominates the USD NAV calculated by Midas'
        `DepositVault <https://github.com/midas-apps/contracts/blob/main/contracts/DepositVault.sol>`__.

        :return:
            The first non-zero payment token in contract-returned order, or
            ``None`` when the scanner must use its off-chain USD fallback.
        """

        payment_tokens = self.fetch_payment_tokens()
        return payment_tokens[0] if payment_tokens else None

    def fetch_denomination_token_address(self) -> HexAddress | None:
        """Return the primary Midas payment-token address.

        Midas issuance vaults may accept several payment tokens. The common
        :class:`VaultBase` schema has one denomination-token field, so this
        method returns the address of :py:meth:`fetch_primary_payment_token`.
        If the contract returns no ERC-20 payment tokens, the scan export uses
        synthetic off-chain USD.

        :return:
            Primary payment-token address, or ``None`` when no token is
            configured.
        """

        denomination_token = self.fetch_denomination_token()
        return denomination_token.address if denomination_token else None

    def fetch_denomination_token(self) -> TokenDetails | None:
        """Expose the primary Midas payment token through :class:`VaultBase`.

        This override fulfils the :class:`VaultBase` denomination-token API for
        a non-ERC-4626 Midas product. It deliberately returns
        :py:meth:`fetch_primary_payment_token`, allowing the shared scanner to
        cache and export the primary accepted payment token in
        :py:attr:`VaultBase.denomination_token`.

        :return:
            Primary payment token, or ``None`` when the scanner must use the
            off-chain USD fallback.
        """

        return self.fetch_primary_payment_token()

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

        denomination_token = self.denomination_token
        return MidasVaultInfo(
            token=self.address,
            chain_id=self.chain_id,
            data_feed=Web3.to_checksum_address(self.product.data_feed),
            oracle=_checksum_or_none(self.product.oracle),
            issuance_vault=_checksum_or_none(self.product.issuance_vault),
            redemption_vault=_checksum_or_none(self.product.redemption_vault),
            denomination_token=denomination_token.address if denomination_token else None,
            synthetic_usd_denomination=denomination_token is None,
            nav_source=MIDAS_NAV_SOURCE,
            nav_estimated=False,
        )

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Return Midas-specific scan-row diagnostics.

        :return:
            Private scan-row fields describing the NAV source and related
            Midas contracts.
        """

        denomination_token = self.denomination_token
        synthetic_usd_denomination = denomination_token is None
        return {
            "Denomination": denomination_token.symbol if denomination_token else "USD",
            "_denomination_token": denomination_token.export() if denomination_token else export_midas_usd_denomination(self.chain_id),
            "_notes": self.get_notes(),
            "_deposit_closed_reason": self.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": self.fetch_redemption_closed_reason(),
            "_nav_source": MIDAS_NAV_SOURCE,
            "_nav_estimated": False,
            "_synthetic_usd_denomination": synthetic_usd_denomination,
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

        metadata = get_handwritten_vault_metadata(self.chain_id, self.address)
        return metadata.link if metadata else MIDAS_HOMEPAGE
