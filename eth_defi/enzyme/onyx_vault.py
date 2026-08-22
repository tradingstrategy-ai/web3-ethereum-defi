"""VaultBase adapter for Enzyme Onyx Shares vaults.

Enzyme's current Base deployment is the modular Onyx protocol. Its ``Shares``
contract is an ERC-20 vault share token, but it deliberately does not implement
ERC-4626. Valuation is published by a connected handler as a stored
18-decimal ``sharePrice()`` in a named accounting unit such as USD. The scanner
normalises the USD reporting unit to native Base USDC for cross-vault metrics;
this does not identify the active handler's deposit asset.

Official Base deployment and ABI source:
https://github.com/enzymefinance/onyx-sdk/tree/main/packages/environment
"""

# ruff: noqa: FBT001, FBT002, PLR0904, PLR0917, PLR6301

from decimal import Decimal
from functools import cached_property
from typing import Literal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.fee import combine_user_facing_management_fee
from eth_defi.enzyme.offchain_metadata import create_enzyme_vault_link, fetch_enzyme_vault_metadata, resolve_enzyme_vault_metadata
from eth_defi.enzyme.onyx_discovery import ENZYME_BASE_CHAIN_ID
from eth_defi.enzyme.onyx_flow import EnzymeVaultFlowManager
from eth_defi.enzyme.onyx_historical import EnzymeVaultHistoricalReader
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositPermission
from eth_defi.vault.fee import FeeData
from eth_defi.vault.lower_case_dict import LowercaseDict

VALUE_ASSET_DECIMALS = 18
FEE_BPS_DENOMINATOR = 10_000
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

#: Onyx ``Shares.getValueAsset()`` is a human-readable accounting label, not
#: an ERC-20 deposit-token address. Every currently discovered Base label has
#: a canonical ERC-20 used only to export comparable scanner metrics. This
#: mapping must reflect the value shown to an investor, not a protocol-internal
#: handler implementation: a handler can accept USDT, EURC, or another asset
#: while the Shares vehicle reports its NAV in USD, BTC, or EUR.
#:
#: Never use these addresses to select a subscription or redemption token. The
#: transaction flow must inspect the active handler instead.
ONYX_VALUE_ASSET_COMPARABILITY_TOKENS: dict[str, HexAddress] = {
    "USD": HexAddress(USDC_NATIVE_TOKEN[ENZYME_BASE_CHAIN_ID]),
    "BTC": HexAddress("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"),  # Base cbBTC
    "EUR": HexAddress("0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42"),  # Base EURC
}

#: Compatibility alias for callers that need the USD reporting normalisation.
ONYX_USD_COMPARABILITY_TOKEN = ONYX_VALUE_ASSET_COMPARABILITY_TOKENS["USD"]


class EnzymeVaultUnsupportedError(RuntimeError):
    """Raised for an Enzyme Onyx feature that is outside this scan adapter."""


class EnzymeVault(VaultBase):
    """Read an Enzyme Onyx Shares vault directly through :class:`VaultBase`.

    The protocol supports both synchronous and queued deposit/redeem handlers.
    This adapter therefore does not advertise a public transaction manager
    until handler-specific flows are implemented and tested.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
        current_deposit_permission: str | None = None,
    ) -> None:
        """Create an Enzyme Onyx vault adapter.

        :param web3:
            Web3 connection for the vault's chain.
        :param spec:
            Chain and canonical Onyx Shares contract address.
        :param token_cache:
            Shared ERC-20 token cache.
        :param features:
            Scanner classification flags.
        :param default_block_identifier:
            Block used for ordinary metadata reads.
        :param require_denomination_token:
            Enforce a resolved reporting denomination in operational callers.
            USD-value Onyx Shares resolve to native Base USDC by the explicit
            comparability assumption documented in
            :meth:`fetch_denomination_token_address`; this does not certify
            USDC as a handler deposit asset.
        :param current_deposit_permission:
            Fixed-block result from the chain-level active handler index. The
            Shares contract cannot enumerate this state on its own.
        """

        super().__init__(token_cache=token_cache, require_denomination_token=require_denomination_token)
        self.web3 = web3
        self.spec = spec
        del features
        self.default_block_identifier = default_block_identifier
        self.current_deposit_permission = VaultDepositPermission(current_deposit_permission) if current_deposit_permission is not None else VaultDepositPermission.unknown
        self.api_metadata = fetch_enzyme_vault_metadata(spec.chain_id, spec.vault_address)

    def _get_block_identifier(self) -> BlockIdentifier:
        """Return the configured metadata block or ``latest``."""

        return self.default_block_identifier or "latest"

    @property
    def chain_id(self) -> int:
        """Return the vault's EVM chain id."""

        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Return the canonical Onyx Shares contract address."""

        return HexAddress(Web3.to_checksum_address(self.spec.vault_address))

    @property
    def vault_address(self) -> HexAddress:
        """Return the scanner-compatible alias for :py:attr:`address`."""

        return self.address

    @cached_property
    def shares_contract(self) -> Contract:
        """Load the Onyx Shares interface at this vault address."""

        return get_deployed_contract(self.web3, "enzyme_onyx/Shares.json", self.address)

    @property
    def name(self) -> str:
        """Return the cached onchain Shares token name."""

        return self.share_token.name or ""

    @property
    def symbol(self) -> str:
        """Return the cached onchain Shares token symbol."""

        return self.share_token.symbol or ""

    @property
    def description(self) -> str | None:
        """Return complete offchain listing copy for this Onyx vault."""

        return resolve_enzyme_vault_metadata("onyx", self.name, self.api_metadata).description

    @property
    def short_description(self) -> str | None:
        """Return complete offchain table copy for this Onyx vault."""

        return resolve_enzyme_vault_metadata("onyx", self.name, self.api_metadata).short_description

    @property
    def manager_name(self) -> str | None:
        """Return a curated manager name when available."""

        return resolve_enzyme_vault_metadata("onyx", self.name, self.api_metadata).manager_name

    def fetch_share_token(self) -> TokenDetails:
        """Fetch the Shares ERC-20 token metadata."""

        token = fetch_erc20_details(
            self.web3,
            self.address,
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Enzyme Onyx Shares vault {self.address}",
        )
        if token is None:
            raise EnzymeVaultUnsupportedError(f"Could not read Enzyme Shares token metadata for {self.address}")
        return token

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the Shares ERC-20 address for the shared historical scanner.

        :param block_identifier:
            Accepted for scanner compatibility; a Shares token is the vault
            contract itself and its address is immutable.
        :return:
            Shares ERC-20 contract address.
        """

        del block_identifier
        return self.address

    def fetch_denomination_token_address(self) -> HexAddress:
        """Map every supported Onyx value asset to a canonical Base ERC-20.

        Onyx ``Shares`` records a named value asset rather than an ERC-20
        denomination token. The scanner needs an ERC-20 denomination to keep
        its share prices, TVL and lifetime metrics comparable with other vaults.
        It deliberately maps each reviewed Base value-asset label to its
        canonical ERC-20: ``USD`` to USDC, ``BTC`` to cbBTC, and ``EUR`` to
        EURC. The mapping covers all 16 Onyx Shares discovered from the
        official Base SharesFactory as of 2026-08-21.

        This is not evidence that the canonical token is the active deposit
        asset. For example, an USD-valued vehicle can use a handler that
        accepts USDT while continuing to report its NAV in USD. Deposit and
        redemption code must inspect the active handler instead of relying on
        this reporting normalisation. An unknown label raises immediately so a
        historical scan cannot proceed with a missing denomination token and
        later fail inside the generic reader state.

        :return:
            Canonical Base ERC-20 used to represent the Shares value asset in
            scanner metrics.
        :raise EnzymeVaultUnsupportedError:
            If the vault is not on Base or Enzyme introduces a value-asset
            label with no reviewed canonical ERC-20 mapping.
        """

        if self.chain_id != ENZYME_BASE_CHAIN_ID:
            raise EnzymeVaultUnsupportedError(f"Enzyme Onyx value-asset mappings are only configured for Base, got chain {self.chain_id}")

        value_asset = self.fetch_value_asset(self._get_block_identifier())
        try:
            return ONYX_VALUE_ASSET_COMPARABILITY_TOKENS[value_asset]
        except KeyError as error:
            supported_labels = ", ".join(sorted(ONYX_VALUE_ASSET_COMPARABILITY_TOKENS))
            message = f"Enzyme Onyx Shares vault {self.address} reports unsupported value asset {value_asset!r}; add a reviewed Base ERC-20 mapping before historical scanning. Supported labels: {supported_labels}"
            raise EnzymeVaultUnsupportedError(message) from error

    def fetch_denomination_token(self) -> TokenDetails:
        """Fetch the ERC-20 selected by the Onyx reporting normalisation.

        The returned token represents the scanner's valuation denomination,
        not a source-proven Onyx handler deposit asset. See
        :meth:`fetch_denomination_token_address` for the USD-to-USDC
        comparability assumption and its transaction-flow limitation.

        :return:
            Metadata for the canonical Base ERC-20 used by the mapped Onyx
            value asset.
        """

        token = fetch_erc20_details(
            self.web3,
            self.fetch_denomination_token_address(),
            chain_id=self.chain_id,
            raise_on_error=False,
            cache=self.token_cache,
            cause_diagnostics_message=f"Enzyme Onyx value asset normalised to a canonical Base ERC-20 for {self.address}",
        )
        if token is None:
            raise EnzymeVaultUnsupportedError(f"Could not read canonical denomination-token metadata for Enzyme Onyx Shares vault {self.address}")
        return token

    def fetch_value_asset(self, block_identifier: BlockIdentifier = "latest") -> str:
        """Read the declared Onyx accounting unit, such as ``USD``.

        :param block_identifier:
            Block number or tag for the metadata read.
        :return:
            Value asset label decoded from the protocol's bytes32 field.
        """

        raw_value_asset = self.shares_contract.functions.getValueAsset().call(block_identifier=block_identifier)
        return bytes(raw_value_asset).rstrip(b"\x00").decode("ascii") or "UNKNOWN"

    def fetch_share_price(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read the stored value-asset price per human-readable share.

        :param block_identifier:
            Block number or tag for the valuation read.
        :return:
            Share price in the named Onyx value asset.
        """

        raw_share_price, _reported_at = self.shares_contract.functions.sharePrice().call(block_identifier=block_identifier)
        return Decimal(raw_share_price) / Decimal(10**VALUE_ASSET_DECIMALS)

    def fetch_total_supply(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Read human-readable ERC-20 share supply."""

        raw_total_supply = self.shares_contract.functions.totalSupply().call(block_identifier=block_identifier)
        return Decimal(raw_total_supply) / Decimal(10**VALUE_ASSET_DECIMALS)

    def fetch_total_assets(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Calculate TVL in the vault's named Onyx value asset."""

        return self.fetch_share_price(block_identifier) * self.fetch_total_supply(block_identifier)

    def fetch_nav(self, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Return TVL in the vault's named value asset.

        :param block_identifier:
            Historical block number or block tag.
        :return:
            TVL expressed in the Onyx value asset.
        """

        return self.fetch_total_assets(block_identifier)

    def fetch_info(self) -> VaultInfo:
        """Fetch the vault address, accounting unit and metric denomination.

        ``denomination_assumption`` records that the named-value-asset mapping
        is a metrics convention. It must not be interpreted as a handler
        deposit asset or as evidence that a user can deposit the mapped token.

        :return:
            Shares address, declared value asset and exported denomination
            assumption.
        """

        return {
            "shares": self.address,
            "value_asset": self.fetch_value_asset(self._get_block_identifier()),
            "denomination_assumption": "Onyx USD, BTC and EUR values are exported as canonical Base USDC, cbBTC and EURC metrics respectively",
        }

    def fetch_portfolio(self, universe: TradingUniverse, block_identifier: BlockIdentifier | None = None) -> VaultPortfolio:
        """Return an empty portfolio until Onyx position trackers are integrated."""

        del universe, block_identifier
        return VaultPortfolio(spot_erc20=LowercaseDict())

    def has_block_range_event_support(self) -> bool:
        """Return false until handler flow events are mapped to Shares vaults."""

        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Return false because the protocol does not make that guarantee."""

        return False

    def get_flow_manager(self) -> VaultFlowManager:
        """Return an explicit unsupported flow reader."""

        return EnzymeVaultFlowManager()

    def get_deposit_manager(self) -> VaultDepositManager:
        """Reject unimplemented handler-specific public transaction flows."""

        message = "Enzyme Onyx deposit and redemption handlers are not implemented"
        raise EnzymeVaultUnsupportedError(message)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the share-price and TVL historical reader.

        Historical deposit and redemption availability remains unsupported;
        see :class:`EnzymeVaultHistoricalReader` for the reason.

        :param stateful: Whether the shared reader should retain adaptive scan
            state between runs.
        :return: Historical share-price and TVL reader.
        """

        return EnzymeVaultHistoricalReader(self, stateful)

    def get_protocol_name(self) -> str:
        """Return the protocol display name."""

        return "Enzyme"

    def is_whitelisted_deposit(self) -> bool:
        """Return current account-approval status from the handler index.

        An Onyx Shares token exposes ``isDepositHandler(address)`` but no
        enumerable handler list. The chain-level Enzyme discovery pass
        reconstructs the active set from ``DepositHandlerAdded`` and
        ``DepositHandlerRemoved`` events, and a batched current-state reader
        inspects the standard synchronous, queued and manual-mint handlers.
        The resulting fixed-block permission is injected into this adapter.

        A directly constructed adapter without that chain context remains
        explicit ``unknown`` rather than performing a costly per-vault event
        scan or assuming the protocol's permissionless default.

        :return:
            ``True`` when every active route requires prior account approval;
            ``False`` when at least one route is proven public or no deposit
            handler is active.
        :raise NotImplementedError:
            If no conclusive chain-level permission snapshot was supplied.
        """

        permission = getattr(self, "current_deposit_permission", VaultDepositPermission.unknown)
        if permission is VaultDepositPermission.unknown:
            raise NotImplementedError(f"Enzyme Onyx Shares vault {self.address} needs a conclusive active deposit-handler index")
        return permission is VaultDepositPermission.whitelisted

    def get_whitelist_notes(self) -> str | None:
        """Explain the current Onyx permission classification.

        The qualification distinguishes investor identity approval from route
        availability and other operating conditions that the shared enum does
        not represent.

        :return:
            Qualification for a resolved chain-level result, or ``None`` when
            the adapter has no conclusive handler snapshot.
        """

        permission = getattr(self, "current_deposit_permission", VaultDepositPermission.unknown)
        if permission is VaultDepositPermission.whitelisted:
            return "Current Enzyme Onyx deposit handlers require prior recipient or controller approval. This is an onchain access rule and does not by itself prove an offchain KYC process."
        if permission is VaultDepositPermission.permissionless:
            return "Current Enzyme Onyx identity policy is permissionless. Either a public deposit route exists or no deposit handler is active; deposit availability, queue settlement, asset checks and other operating conditions are separate."
        return None

    def fetch_fee_tracker_rate(
        self,
        tracker_getter: Literal["getManagementFeeTracker", "getPerformanceFeeTracker"],
        block_identifier: BlockIdentifier,
        fee_handler: Contract | None = None,
    ) -> Percent | None:
        """Read a current annual fee rate from an optional Onyx fee component.

        Onyx attaches fee components per Shares contract. A configured
        ``FeeHandler`` points to the optional management and performance fee
        trackers, whose rates are stored in basis points. This is intentionally
        a current-state read only: fee handlers and trackers can be replaced,
        so historical fee data needs component-change event handling.

        :param tracker_getter:
            FeeHandler accessor selecting the management or performance
            tracker.
        :param block_identifier:
            Block number or tag for the current metadata scan.
        :param fee_handler:
            Already loaded handler, allowing a single metadata refresh to
            reuse the Shares ``getFeeHandler()`` result.
        :return:
            Annual fee fraction, such as ``0.01`` for 1%, or ``None`` when
            the Shares vehicle does not configure this tracker.
        """

        fee_handler = fee_handler or self.fetch_fee_handler(block_identifier)
        if fee_handler is None:
            return None

        fee_tracker_address = getattr(fee_handler.functions, tracker_getter)().call(block_identifier=block_identifier)
        if fee_tracker_address.lower() == ZERO_ADDRESS:
            return None

        fee_tracker = get_deployed_contract(self.web3, "enzyme_onyx/ContinuousFlatRateFeeTracker.json", fee_tracker_address)
        fee_bps = fee_tracker.functions.getRate().call(block_identifier=block_identifier)
        return Percent(float(Decimal(fee_bps) / Decimal(FEE_BPS_DENOMINATOR)))

    def fetch_fee_handler(self, block_identifier: BlockIdentifier) -> Contract | None:
        """Load the optional current FeeHandler component.

        Each Onyx Shares token may omit the component entirely. In that case,
        neither dynamic nor entrance/exit fees are configured. The component
        can be replaced, so callers must use it only for a point-in-time read.

        :param block_identifier:
            Block number or tag for the component-address read.
        :return:
            FeeHandler contract, or ``None`` when not configured.
        """

        fee_handler_address = self.shares_contract.functions.getFeeHandler().call(block_identifier=block_identifier)
        if fee_handler_address.lower() == ZERO_ADDRESS:
            return None
        return get_deployed_contract(self.web3, "enzyme_onyx/FeeHandler.json", fee_handler_address)

    def fetch_fee_handler_bps(
        self,
        fee_getter: Literal["getEntranceFeeBps", "getExitFeeBps"],
        block_identifier: BlockIdentifier,
        fee_handler: Contract | None = None,
    ) -> Percent | None:
        """Read the current entrance or exit fee from the FeeHandler.

        Enzyme calculates these fees in shares at the configured basis-point
        rate, reducing the investor's net issued or redeemed shares. The rate
        is a current-state value only; historical values require
        ``EntranceFeeSet`` and ``ExitFeeSet`` event processing.

        :param fee_getter:
            FeeHandler accessor selecting the entrance or exit rate.
        :param block_identifier:
            Block number or tag for the metadata scan.
        :param fee_handler:
            Already loaded handler for a batched metadata read.
        :return:
            Fee fraction, or ``None`` when no FeeHandler is configured.
        """

        fee_handler = fee_handler or self.fetch_fee_handler(block_identifier)
        if fee_handler is None:
            return None
        fee_bps = getattr(fee_handler.functions, fee_getter)().call(block_identifier=block_identifier)
        return Percent(float(Decimal(fee_bps) / Decimal(FEE_BPS_DENOMINATOR)))

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Read the current annual management fee from the configured tracker.

        :param block_identifier:
            Block number or tag for the metadata scan.
        :return:
            Annual management-fee fraction, or ``None`` when absent.
        """

        return self.fetch_fee_tracker_rate("getManagementFeeTracker", block_identifier)

    def fetch_protocol_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the additional current Onyx protocol fee when configured.

        The reviewed standard Onyx FeeHandler exposes management, performance,
        entrance and exit fee settings, but no distinct protocol-fee rate. The
        explicit ``None`` keeps the shared user-facing aggregation path aligned
        with Blue without inventing a platform charge from internal fee-recipient
        details.

        :param block_identifier:
            Accepted for a consistent point-in-time fee-reader interface.
        :return:
            ``None`` until a reviewed Onyx contract exposes a separate protocol
            management-fee rate.
        """

        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Read the current performance fee from the configured tracker.

        :param block_identifier:
            Block number or tag for the metadata scan.
        :return:
            Performance-fee fraction, or ``None`` when absent.
        """

        return self.fetch_fee_tracker_rate("getPerformanceFeeTracker", block_identifier)

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Read the current entrance fee as a fraction of gross issued shares.

        :param block_identifier:
            Block number or tag for the metadata scan.
        :return:
            Entrance-fee fraction, or ``None`` when no FeeHandler is present.
        """

        return self.fetch_fee_handler_bps("getEntranceFeeBps", block_identifier)

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Read the current exit fee as a fraction of gross redeemed shares.

        :param block_identifier:
            Block number or tag for the metadata scan.
        :return:
            Exit-fee fraction, or ``None`` when no FeeHandler is present.
        """

        return self.fetch_fee_handler_bps("getExitFeeBps", block_identifier)

    def get_fee_data(self) -> FeeData:
        """Read all currently configured Enzyme fee classes efficiently.

        The four rate reads share the same FeeHandler. Reading it once avoids
        three redundant Shares calls during scanner metadata refreshes. The
        standard Onyx FeeHandler has no separate protocol-fee rate, but its
        management rate still passes through the same user-facing aggregation
        as Blue. This preserves correct output if a reviewed Onyx protocol fee
        is added later.

        :return:
            Current management, performance, entrance and exit fee data.
        """

        block_identifier = self._get_block_identifier()
        fee_handler = self.fetch_fee_handler(block_identifier)
        manager_fee = self.fetch_fee_tracker_rate("getManagementFeeTracker", block_identifier, fee_handler)
        protocol_fee = self.fetch_protocol_fee(block_identifier)
        return FeeData(
            fee_mode=self.get_fee_mode(),
            management=combine_user_facing_management_fee(manager_fee, protocol_fee),
            performance=self.fetch_fee_tracker_rate("getPerformanceFeeTracker", block_identifier, fee_handler),
            deposit=self.fetch_fee_handler_bps("getEntranceFeeBps", block_identifier, fee_handler),
            withdraw=self.fetch_fee_handler_bps("getExitFeeBps", block_identifier, fee_handler),
            protocol=protocol_fee,
        )

    def get_link(self, referral: str | None = None) -> str:
        """Return the address-specific Enzyme Onyx application page.

        The canonical Shares address identifies the vehicle in the Enzyme
        application. The network query distinguishes the Base deployment and
        keeps the URL format aligned with Blue vault detail pages.

        :param referral: Accepted for the shared vault interface; Enzyme does
            not expose a reviewed referral query parameter.
        :return: Direct Enzyme application URL for this Onyx Shares vault.
        """

        del referral
        return create_enzyme_vault_link(self.chain_id, self.address)
