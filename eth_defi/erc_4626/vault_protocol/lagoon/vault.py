"""Lagoon Finance vault adapter.

The adapter detects explicit Lagoon releases through ``version()`` and falls
back to the historical ``pendingSilo()`` probe for deployments without a
version getter. Lagoon v0.6 and the characterised v1.0 deployment share the
official v0.6 read interface used here. The v1 implementation is unverified,
so compatibility is limited to the calls and storage fields covered by the
fixed-block Base integration test.

Deployment support remains pinned to Lagoon v0.5 artefacts. See the
`official Lagoon source <https://github.com/hopperlabsxyz/lagoon-v0>`__ and the
ABI provenance in :file:`eth_defi/abi/lagoon/README.md`.
"""

import datetime
import enum
import logging
from dataclasses import asdict
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

import eth_abi
from eth.typing import BlockRange
from eth_abi.exceptions import InsufficientDataBytes
from eth_typing import BlockIdentifier, ChecksumAddress, HexAddress
from hexbytes import HexBytes
from safe_eth.safe import Safe
from safe_eth.safe.exceptions import CannotRetrieveSafeInfoException
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR, encode_function_call, get_deployed_contract, get_function_abi_by_name, get_function_selector, present_solidity_args
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import LagoonVaultMetadata, fetch_lagoon_vault_metadata
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.types import Percent
from eth_defi.vault.base import VaultFlowManager, VaultInfo, VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import MISSING_IN_PROTOCOL_FRONTEND, VaultFlag

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager


logger = logging.getLogger(__name__)

#: How much gas we use for valuation post
DEFAULT_LAGOON_POST_VALUATION_GAS = 500_000

#: How much gas we use for valuation post
DEFAULT_LAGOON_SETTLE_GAS = 500_000

#: Lagoon fee rates use basis points, where 10,000 is 100%.
LAGOON_FEE_RATE_DENOMINATOR = 10_000

#: JSON-RPC error code used for an EVM execution revert.
JSON_RPC_EXECUTION_REVERT_CODE = 3

#: Minimal interface for the Lagoon v0.6-compatible access-policy view.
#:
#: The canonical definition is v0.6 source. The production v1 deployment is
#: covered only through fixed-block characterisation because its implementation
#: source is not verified.
#:
#: https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/Accessable.sol
LAGOON_MODERN_ACCESS_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "isAllowed",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def _is_empty_execution_revert(error: ExtraValueError) -> bool:
    """Check whether a provider error represents a missing-view empty revert.

    The multi-provider wrapper raises :class:`ExtraValueError` both for
    malformed RPC responses and for deterministic EVM reverts.  Lagoon v0.5's
    removed ``isWhitelistActivated()`` getter has the specific response
    ``code=3``, ``message=execution reverted``, ``data=0x``.  Only that shape
    may activate the version-gated sentinel fallback.

    :param error:
        Provider response error raised by the whitelist policy call.
    :return:
        ``True`` only for the deterministic empty EVM revert shape.
    """
    if not error.args or not isinstance(error.args[0], dict):
        return False
    response = error.args[0]
    return response.get("code") == JSON_RPC_EXECUTION_REVERT_CODE and response.get("data") == "0x" and "execution reverted" in str(response.get("message", "")).lower()


class LagoonVaultInfo(VaultInfo):
    """Capture information about Lagoon vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    asset: HexAddress

    #: Lagoon vault deployment info
    safe: HexAddress
    #: Lagoon vault deployment info
    whitelistManager: HexAddress  # noqa: N815 - Preserve the public info-dict key.
    #: Lagoon vault deployment info
    feeReceiver: HexAddress  # noqa: N815 - Preserve the public info-dict key.
    #: Lagoon vault deployment info
    feeRegistry: HexAddress  # noqa: N815 - Preserve the public info-dict key.
    #: Lagoon vault deployment info
    valuationManager: HexAddress  # noqa: N815 - Preserve the public info-dict key.

    #: Safe multisig core info
    address: ChecksumAddress
    #: Safe multisig core info
    fallback_handler: ChecksumAddress
    #: Safe multisig core info
    guard: ChecksumAddress
    #: Safe multisig core info
    master_copy: ChecksumAddress
    #: Safe multisig core info
    modules: list[ChecksumAddress]
    #: Safe multisig core info
    nonce: int
    #: Safe multisig core info
    owners: list[ChecksumAddress]
    #: Safe multisig core info
    threshold: int
    #: Safe multisig core info
    version: str


class LagoonVersion(enum.Enum):
    """Figure out Lagoon version."""

    legacy = "legacy"
    v_0_5_0 = "v0.5.0"
    v_0_4_0 = "v0.4.0"
    v_0_6_0 = "v0.6.0"
    v_1_0_0 = "v1.0.0"


#: Versions handled through the observed v0.6-compatible read surface.
LAGOON_MODERN_VERSIONS: frozenset[LagoonVersion] = frozenset(
    {
        LagoonVersion.v_0_6_0,
        LagoonVersion.v_1_0_0,
    }
)

#: ERC-7201 ``hopper.storage.Roles`` slot from the official Lagoon v0.6 source.
#: https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/RolesLib.sol
LAGOON_MODERN_ROLES_STORAGE_SLOT = int(
    "0x7c302ed2c673c3d6b4551cf74a01ee649f887e14fd20d13dbca1b6099534d900",
    16,
)

#: ERC-7201 ``hopper.storage.ERC7540`` slot plus the ``pendingSilo`` field's
#: eight-slot offset in the official Lagoon v0.6 storage layout.
#: https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/ERC7540Lib.sol
LAGOON_PENDING_SILO_STORAGE_SLOT = int(
    "0x5c74d456014b1c0eb4368d944667a568313858a3029a650ff0cb7b56f8b57a08",
    16,
)

#: ABI selected after ``version()`` detection. The adapter historically used
#: the v0.5 ABI for v0.4. The unverified v1 deployment uses the official v0.6
#: ABI only as a fixed-block-tested compatibility interface.
LAGOON_VAULT_ABI_BY_VERSION: dict[LagoonVersion, str] = {
    LagoonVersion.legacy: "lagoon/Vault.json",
    LagoonVersion.v_0_4_0: "lagoon/v0.5.0/Vault.json",
    LagoonVersion.v_0_5_0: "lagoon/v0.5.0/Vault.json",
    LagoonVersion.v_0_6_0: "lagoon/v0.6.0/Vault.json",
    LagoonVersion.v_1_0_0: "lagoon/v0.6.0/Vault.json",
}


class AutomatedSafe:
    """Mixin for Safe multisig wallets with TradingStrategyModuleV0 guard.

    Provides transaction wrapping through the guard module,
    used by both full Lagoon vaults (:class:`LagoonVault`) and
    satellite Safe-only deployments (:class:`LagoonSatelliteVault`).

    - Encapsulates Safe + TradingStrategyModuleV0 interaction
    - Independent of vault contracts (no ERC-4626 dependency)
    """

    def __init__(
        self,
        web3: Web3,
        safe_address: HexAddress | None = None,
        trading_strategy_module_address: HexAddress | None = None,
    ):
        """
        :param web3:
            Web3 connection to the chain where the Safe is deployed.

        :param safe_address:
            Address of the Gnosis Safe multisig.
            Can be None for :class:`LagoonVault` where it is lazily
            resolved from the vault contract.

        :param trading_strategy_module_address:
            Address of the TradingStrategyModuleV0 guard module enabled on the Safe.
        """
        self._automated_safe_web3 = web3
        self._automated_safe_address = safe_address
        self._trading_strategy_module_address = trading_strategy_module_address

    @property
    def safe_address(self) -> HexAddress:
        """Get Safe multisig contract address."""
        assert self._automated_safe_address is not None, "Safe address not set"
        return self._automated_safe_address

    @property
    def trading_strategy_module_address(self) -> HexAddress | None:
        """Get TradingStrategyModuleV0 contract address."""
        return self._trading_strategy_module_address

    @trading_strategy_module_address.setter
    def trading_strategy_module_address(self, value: HexAddress | None):
        self._trading_strategy_module_address = value

    def fetch_safe(self, address: HexAddress | str) -> Safe:
        """Create a Safe object from an address.

        Use :py:attr:`safe` property for cached access.
        """
        client = create_safe_ethereum_client(self._automated_safe_web3)
        return Safe(
            address,
            client,
        )

    @cached_property
    def safe(self) -> Safe:
        """Get the underlying Safe object used as an API from safe-eth-py library.

        - Wraps Safe contract using Gnosis's in-house library
        """
        return self.fetch_safe(self.safe_address)

    @cached_property
    def safe_contract(self) -> Contract:
        """Safe multisig as a contract.

        - Interact with Safe multisig ABI
        """
        return self.safe.contract

    @cached_property
    def trading_strategy_module(self) -> Contract:
        """Get the TradingStrategyModuleV0 contract instance."""
        assert self._trading_strategy_module_address, "TradingStrategyModuleV0 address must be separately given in the configuration"
        return get_deployed_contract(
            self._automated_safe_web3,
            "safe-integration/TradingStrategyModuleV0.json",
            self._trading_strategy_module_address,
        )

    def fetch_trading_strategy_module_version(self) -> str | None:
        """Perform deployed smart contract probing.

        :return:
            v0.1.0 or v0.1.1.

            None if not TS module associated.
        """

        if not self._trading_strategy_module_address:
            return None

        probe_call = EncodedCall.from_keccak_signature(
            function="getTradingStrategyModuleVersion",
            address=Web3.to_checksum_address(self._trading_strategy_module_address),
            signature=Web3.keccak(text="getTradingStrategyModuleVersion()")[0:4],
            data=b"",
            extra_data={},
        )

        try:
            version_bytes = probe_call.call(self._automated_safe_web3, block_identifier="latest")
            return version_bytes.decode("utf-8")
        except (ValueError, ContractLogicError):
            # getTradingStrategyModuleVersion() was not yet created
            return "v0.1.0"

    @cached_property
    def trading_strategy_module_version(self) -> str:
        """Get TradingStrategyModuleV0 contract ABI version.

        - Subject to change, development in progress
        """
        version = self.fetch_trading_strategy_module_version()
        return version

    def transact_via_exec_module(
        self,
        func_call: ContractFunction,
        value: int = 0,
        operation=0,
    ) -> ContractFunction:
        """Create a multisig transaction using a module.

        - Calls ``execTransactionFromModule`` on Gnosis Safe contract

        - Executes a transaction as a multisig

        - Mostly used for testing w/whitelist ignore

        .. warning ::

            A special gas fix is needed, because ``eth_estimateGas`` seems to fail for these Gnosis Safe transactions.

        :param func_call:
            Bound smart contract function call

        :param value:
            ETH attached to the transaction

        :param operation:
            Gnosis enum.

            Call = 0, DelegateCall = 1.
        """
        contract_address = func_call.address
        data_payload = encode_function_call(func_call, func_call.arguments)
        contract = self.safe_contract
        bound_func = contract.functions.execTransactionFromModule(
            contract_address,
            value,
            data_payload,
            operation,
        )
        return bound_func

    def transact_via_trading_strategy_module(
        self,
        func_call: ContractFunction,
        value: int = 0,
        abi_version: str | None = None,
    ) -> ContractFunction:
        """Create a Safe multisig transaction using TradingStrategyModuleV0.

        :param func_call:
            Bound smart contract function call

        :param value:
            ETH value attached to the call.

        :param abi_version:
            Use specific TradingStrategyModuleV0 ABI version.

        :return:
            Bound Solidity function call you need to turn to a transaction
        """
        assert self._trading_strategy_module_address is not None, f"TradingStrategyModuleV0 address not set for {self.safe_address}"
        contract_address = func_call.address
        data_payload = encode_function_call(func_call, func_call.arguments)

        module_version = abi_version or self.trading_strategy_module_version

        logger.info(
            "Lagoon: Wrapping call to TradingStrategyModuleV0 %s. Target: %s, function: %s (0x%s), args: %s, payload is %d bytes",
            module_version,
            contract_address,
            func_call.fn_name,
            get_function_selector(func_call).hex(),
            present_solidity_args(func_call.arguments),
            len(data_payload),
        )

        if module_version == "v0.1.0":
            bound_func = self.trading_strategy_module.functions.performCall(
                contract_address,
                data_payload,
            )
        else:
            # Value parameter was added for Orderly
            bound_func = self.trading_strategy_module.functions.performCall(
                contract_address,
                data_payload,
                value,
            )
        return bound_func


class LagoonSatelliteVault(AutomatedSafe):
    """A satellite chain deployment: Safe + TradingStrategyModuleV0, no vault contract.

    Used on chains that only receive bridged assets and execute trades,
    without deposit/redemption infrastructure (no ERC-4626 vault).
    """

    def __init__(
        self,
        web3: Web3,
        safe_address: HexAddress,
        trading_strategy_module_address: HexAddress,
    ):
        super().__init__(web3, safe_address, trading_strategy_module_address)

    @property
    def web3(self) -> Web3:
        """Get Web3 connection for this satellite chain."""
        return self._automated_safe_web3


class LagoonVault(ERC7540Vault, AutomatedSafe):  # noqa: PLR0904 - Protocol adapter surface mirrors the base vault API.
    """Python interface for interacting with Lagoon Finance vaults.

    Lagoon separates managed assets in a Safe from asynchronous requests held
    by a pending Silo. The Silo's denomination-token balance represents pending
    deposits, while its vault-share balance represents pending redemptions.
    Settlements publish a new total-asset valuation and process queued requests.

    See :class:`~eth_defi.vault.base.VaultBase` for the common vault API and
    `ERC-7540 <https://eips.ethereum.org/EIPS/eip-7540>`__ for the asynchronous
    request lifecycle.
    """

    def __init__(  # noqa: PLR0917 - Constructor follows the shared vault adapter API.
        self,
        web3: Web3,
        spec: VaultSpec,
        trading_strategy_module_address: HexAddress | None = None,
        token_cache: dict | None = None,
        vault_abi: str | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        **kwargs,
    ):
        """
        :param spec:
            Address must be Lagoon vault  address (not Safe address)

        :param trading_strategy_module_address:
            TradingStrategyModuleV0 enabled on Safe for automated trading.

            If not given, not known.

        :param vault_abi:
            ABI filename we use.

            Lagoon has different versions.

            None = autodetect.

        :param default_block_identifier:
            Override block identifier for on-chain metadata reads.

            See :py:class:`ERC4626Vault` for details.
        """
        ERC7540Vault.__init__(self, web3, spec, features=features or {ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like}, token_cache=token_cache, default_block_identifier=default_block_identifier, **kwargs)
        AutomatedSafe.__init__(self, web3, safe_address=None, trading_strategy_module_address=trading_strategy_module_address)

        if vault_abi is None:
            version = self.version
            vault_abi = LAGOON_VAULT_ABI_BY_VERSION[version]

        self.vault_abi = vault_abi
        self.check_version_compatibility()

    def __repr__(self):
        return f"<Lagoon vault:{self.vault_contract.address} safe:{self.safe_address}>"

    def fetch_version(self) -> LagoonVersion:
        """Read and classify the deployed Lagoon version.

        Explicit version strings are mapped through :class:`LagoonVersion`.
        Deployments without ``version()`` retain the historical
        ``pendingSilo()`` probe: a successful call is legacy, while an empty
        revert identifies the v0.5-compatible family.

        Both probes use :attr:`default_block_identifier` when supplied so
        proxy upgrades cannot mix current and historical state.

        :return:
            Supported Lagoon version family.
        :raise NotImplementedError:
            If ``version()`` returns a release this adapter does not support.
        """

        block_identifier = self._get_block_identifier()
        probe_call = EncodedCall.from_keccak_signature(
            function="version",
            address=Web3.to_checksum_address(self.spec.vault_address),
            signature=Web3.keccak(text="version()")[0:4],
            data=b"",
            extra_data={},
        )
        try:
            result = probe_call.call(self.web3, block_identifier=block_identifier)
            decoded = eth_abi.decode(["string"], result)
            decoded_version = decoded[0]
        except (ValueError, ContractLogicError, InsufficientDataBytes):
            pass
        else:
            try:
                return LagoonVersion(decoded_version)
            except ValueError as e:
                message = f"Unknown Lagoon version {decoded_version} for vault {self.spec.vault_address} on chain {self.chain_id} at block {block_identifier}"
                raise NotImplementedError(message) from e

        probe_call = EncodedCall.from_keccak_signature(
            function="pendingSilo",
            address=Web3.to_checksum_address(self.spec.vault_address),
            signature=Web3.keccak(text="pendingSilo()")[0:4],
            data=b"",
            extra_data={},
        )

        try:
            probe_call.call(self.web3, block_identifier=block_identifier)
            return LagoonVersion.legacy
        except (ValueError, ContractLogicError, InsufficientDataBytes):
            return LagoonVersion.v_0_5_0

    def check_version_compatibility(self):
        """Throw if there is mismatch between ABI and contract exposed EVM calls"""
        if self.version != LagoonVersion.legacy:
            # Check we have correct ABI file loaded
            settle_deposit_abi = get_function_abi_by_name(self.vault_contract, "settleDeposit")
            # function settleDeposit(uint256 _newTotalAssets) public override onlySafe onlyOpen {
            assert len(settle_deposit_abi["inputs"]) == 1, f"Wrong old Lagoon ABI file loaded for {self.vault_address}"

        # We have one broken Lagoon deployment on Arbitrum with 0x0 as Safe address
        # assert self.safe is not None, f"Safe multisig address is not set for {self.vault_address}"

    @cached_property
    def version(self) -> LagoonVersion:
        """Return the cached deployed Lagoon version.

        The first access performs the network probes in
        :meth:`fetch_version`; subsequent accesses reuse the result.

        :return:
            Supported Lagoon version family.
        """
        return self.fetch_version()

    @cached_property
    def lagoon_metadata(self) -> LagoonVaultMetadata | None:
        """Offchain metadata from Lagoon's web app API.

        - Fetched from ``app.lagoon.finance/api/vault`` endpoint
        - Cached on first access
        - Returns None if vault is not in Lagoon's app database
        """
        return fetch_lagoon_vault_metadata(self.web3, self.vault_address)

    @property
    def description(self) -> str | None:
        """Full vault strategy description from Lagoon's offchain metadata."""
        if self.lagoon_metadata:
            return self.lagoon_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """Short one-liner vault summary from Lagoon's offchain metadata."""
        if self.lagoon_metadata:
            return self.lagoon_metadata.get("short_description")
        return None

    @property
    def manager_name(self) -> str | None:
        """Lagoon curator names from Lagoon's offchain vault API."""
        if not self.lagoon_metadata:
            return None

        names = (name for curator in self.lagoon_metadata.get("curators", []) if (name := (curator.get("name") or "").strip()))
        return ", ".join(names) or None

    def get_flags(self) -> set[VaultFlag]:
        """Get vault flags, auto-flagging vaults missing from Lagoon's frontend.

        - If the vault has no metadata in Lagoon's API, it is flagged as ``unofficial``
        - Manual flags from :py:data:`~eth_defi.vault.flag.VAULT_FLAGS_AND_NOTES` take precedence
        """
        flags = super().get_flags()
        if flags:
            return flags
        if self.lagoon_metadata is None:
            return {VaultFlag.unofficial}
        return flags

    def get_notes(self) -> str | None:
        """Get notes for this vault.

        - Returns manual notes from the vault flags if set
        - If vault is missing from Lagoon's frontend, returns the missing note
        - Otherwise falls back to the full description from Lagoon's offchain metadata
        """
        manual_notes = super().get_notes()
        if manual_notes:
            return manual_notes
        if self.lagoon_metadata is None:
            return MISSING_IN_PROTOCOL_FRONTEND
        return self.description

    def get_withdrawal_period(self) -> WithdrawalPeriod | None:
        """Return Lagoon's non-binding curator settlement estimate for backtesting.

        Lagoon redemptions follow the asynchronous `ERC-7540 lifecycle
        <https://eips.ethereum.org/EIPS/eip-7540>`__: a request is only
        claimable after the curator posts a valuation and the Safe settles the
        batch. The contracts do not impose a time-bound settlement deadline.

        The official Lagoon app supplies ``averageSettlement`` in its offchain
        vault metadata. It describes the curator's estimated cadence for
        deposit and redemption settlement cycles, not a contractual promise.
        A curator may settle earlier, later, or not at all. Therefore the
        binding ``min_period`` and ``max_period`` fields are ``None`` and only
        ``estimated_settlement`` is populated for backtesting.

        :return:
            Asynchronous withdrawal metadata with an offchain settlement
            estimate, or ``None`` if Lagoon has no valid estimate for the
            vault.
        """
        metadata = self.lagoon_metadata
        average_settlement = metadata.get("average_settlement") if metadata else None
        if not isinstance(average_settlement, int) or average_settlement <= 0:
            return None
        return WithdrawalPeriod(
            min_period=None,
            max_period=None,
            delay_type=WithdrawalDelayType.delay,
            estimated_settlement=datetime.timedelta(seconds=average_settlement),
        )

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_contract(
            self.web3,
            self.vault_abi,
            self.spec.vault_address,
        )

    @cached_property
    def whitelist_contract(self) -> Contract:
        """Get the stable whitelist interface at the vault address.

        Lagoon's version-specific vault ABIs do not consistently include the
        inherited whitelist views.  The shared interface contains both
        selectors and can be safely bound to every Lagoon vault deployment.
        Unsupported selectors are translated to ``NotImplementedError`` by
        the public accessors below.
        """
        return get_deployed_contract(
            self.web3,
            "lagoon/Whitelistable.json",
            self.spec.vault_address,
        )

    @cached_property
    def access_contract(self) -> Contract:
        """Bind the v0.6-compatible ``isAllowed(address)`` interface.

        Lagoon v0.6 replaced ``Whitelistable`` with the canonical
        `Accessable contract
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/Accessable.sol>`__.
        The characterised v1 deployment exposes the same selector. Its
        implementation is unverified, so the adapter does not infer any wider
        v1 access-control guarantees from this compatibility call.

        :return:
            Contract proxy exposing ``isAllowed(address)``.
        """
        return self.web3.eth.contract(
            address=Web3.to_checksum_address(self.spec.vault_address),
            abi=LAGOON_MODERN_ACCESS_ABI,
        )

    def is_whitelisted_deposit(self) -> bool:
        """Determine whether a Lagoon vault uses whitelist-mode deposits.

        Lagoon released ``isWhitelistActivated()`` in
        `v0.3.0 <https://github.com/hopperlabsxyz/lagoon-v0/releases/tag/v0.3.0>`__
        and retained it in the canonical
        `v0.4.0 Whitelistable source <https://github.com/hopperlabsxyz/lagoon-v0/blob/v0.4.0/src/v0.4.0/Whitelistable.sol>`__.
        The getter was removed from the
        `v0.5.0 Whitelistable source <https://github.com/hopperlabsxyz/lagoon-v0/blob/v0.5.0/src/v0.5.0/Whitelistable.sol>`__,
        despite ``isWhitelisted(address)`` remaining available.  For v0.5
        deployments only, use the zero-address sentinel: its ``False`` result
        means whitelist enforcement is active, while ``True`` means deposits
        are permissionless.  This follows the v0.5 implementation's
        ``isWhitelisted`` branch for a disabled whitelist.

        Lagoon v0.6 replaces ``Whitelistable`` with an access layer supporting
        whitelist mode, blacklist mode and an external sanctions oracle. Its
        canonical `AccessableLib.isAllowed implementation
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/AccessableLib.sol#L131-L161>`__
        returns ``False`` for the zero address in whitelist mode and ``True``
        under the default-open blacklist mode. Therefore the modern adapter
        uses ``isAllowed(0x0)`` as its version-specific sentinel. The v1 route
        relies only on fixed-block compatibility with this selector, not on
        verified v1 source. Individual account admission must still be checked
        because an otherwise default-open account may be denied.

        :return:
            ``True`` when the vault uses whitelist mode.

        :raise NotImplementedError:
            If the deployed Lagoon version exposes neither the policy getter
            nor its version-specific account-access fallback.
        """
        if self.version in LAGOON_MODERN_VERSIONS:
            try:
                # The zero address can never submit a transaction and granting
                # it explicit access has no meaningful use.
                return not self.is_account_whitelisted(ZERO_ADDRESS_STR)
            except NotImplementedError as e:
                raise NotImplementedError(f"Lagoon {self.version.value} vault {self.address} does not expose isAllowed(address)") from e

        try:
            return bool(self.whitelist_contract.functions.isWhitelistActivated().call(block_identifier=self._get_block_identifier()))
        except ExtraValueError as e:
            if not _is_empty_execution_revert(e):
                raise
            getter_error = e
        except (BadFunctionCallOutput, ContractLogicError) as e:
            getter_error = e

        if self.version != LagoonVersion.v_0_5_0:
            raise NotImplementedError(f"Lagoon {self.version.value} vault {self.address} does not expose isWhitelistActivated()") from getter_error
        try:
            # The zero address can never submit a transaction and will never
            # be whitelisted because granting it admission has no meaning.
            return not self.is_account_whitelisted(ZERO_ADDRESS_STR)
        except NotImplementedError:
            raise NotImplementedError(f"Lagoon vault {self.address} does not expose usable whitelist policy views") from getter_error

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Determine whether an account passes Lagoon's versioned access view.

        In the versioned v0.5 implementation, the canonical
        `isWhitelisted source <https://github.com/hopperlabsxyz/lagoon-v0/blob/v0.5.0/src/v0.5.0/Whitelistable.sol>`__
        returns ``True`` for every account when the whitelist is disabled and
        returns mapping membership when it is active.  Thus this probe is a
        reliable whitelist-admission result even where the v0.5 deployment
        lacks ``isWhitelistActivated()``.  It does not establish allowance,
        capacity, pause state, or an open ERC-7540 request window.

        Lagoon v0.6 instead calls ``isAllowed(address)``. The canonical
        `versioned AccessableLib implementation
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/AccessableLib.sol#L131-L161>`__
        combines whitelist or blacklist mode with an optional external
        sanctions oracle. Thus this method's historical name means "admitted
        by Lagoon's access policy" for the v0.6-compatible route. The v1
        implementation is unverified and is supported only to the extent
        covered by the fixed-block integration test.

        :param address:
            Account whose access status is queried.

        :return:
            ``True`` when Lagoon's versioned access policy accepts the account.

        :raise NotImplementedError:
            If the deployed Lagoon version does not expose its expected access
            getter.
        """
        block_identifier = self._get_block_identifier()
        if self.version in LAGOON_MODERN_VERSIONS:
            try:
                return bool(self.access_contract.functions.isAllowed(Web3.to_checksum_address(address)).call(block_identifier=block_identifier))
            except ExtraValueError as e:
                if not _is_empty_execution_revert(e):
                    raise
                raise NotImplementedError(f"Lagoon {self.version.value} vault {self.address} does not expose isAllowed(address)") from e
            except (BadFunctionCallOutput, ContractLogicError) as e:
                raise NotImplementedError(f"Lagoon {self.version.value} vault {self.address} does not expose isAllowed(address)") from e

        try:
            return bool(self.whitelist_contract.functions.isWhitelisted(Web3.to_checksum_address(address)).call(block_identifier=block_identifier))
        except ExtraValueError as e:
            if not _is_empty_execution_revert(e):
                raise
            raise NotImplementedError(f"Lagoon vault {self.address} does not expose isWhitelisted(address)") from e
        except (BadFunctionCallOutput, ContractLogicError) as e:
            raise NotImplementedError(f"Lagoon vault {self.address} does not expose isWhitelisted(address)") from e

    def has_block_range_event_support(self):  # noqa: PLR6301 - Implements the instance-level base API.
        return True

    def has_deposit_distribution_to_all_positions(self):  # noqa: PLR6301 - Implements the instance-level base API.
        return False

    def get_flow_manager(self) -> "LagoonFlowManager":
        return LagoonFlowManager(self)

    def fetch_vault_info(self) -> dict:
        """Get all information we can extract from the vault smart contracts."""
        vault = self.vault_contract
        block_identifier = self._get_block_identifier()
        try:
            if self.version in LAGOON_MODERN_VERSIONS:
                roles_tuple = self._fetch_modern_roles(block_identifier)
            else:
                roles_tuple = vault.functions.getRolesStorage().call(block_identifier=block_identifier)
            whitelist_manager, fee_receiver, safe, fee_registry, valuation_manager = roles_tuple
            broken = False
        except (ValueError, BadFunctionCallOutput, ExtraValueError) as e:
            logger.error("Failed to fetch Lagoon roles for vault %s at block %s, error: %s", self.vault_address, block_identifier, e, exc_info=e)
            whitelist_manager = fee_receiver = safe = fee_registry = valuation_manager = None
            broken = True

        asset = vault.functions.asset().call(block_identifier=block_identifier)
        return {
            "address": vault.address,
            "whitelistManager": whitelist_manager,
            "feeReceiver": fee_receiver,
            "feeRegistry": fee_registry,
            "valuationManager": valuation_manager,
            "safe": safe,
            "asset": asset,
            "tradingStrategyModuleAddress": self.trading_strategy_module_address,
            "broken": broken,
        }

    def _fetch_address_from_storage(self, slot: int, block_identifier: BlockIdentifier) -> HexAddress:
        """Read an address stored in the low 20 bytes of an EVM storage slot.

        The official `v0.6 role storage layout
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/RolesLib.sol>`__
        stores each role address in its own Solidity word. The pending Silo
        address uses the same low-20-byte encoding in its ERC-7540 namespace.

        :param slot:
            Storage slot to read from the vault proxy.
        :param block_identifier:
            Historical block at which to read the slot.
        :return:
            Checksummed address decoded from the slot.
        """
        value = self.web3.eth.get_storage_at(
            self.vault_address,
            slot,
            block_identifier=block_identifier,
        )
        return Web3.to_checksum_address(value[-20:])

    def _fetch_modern_roles(self, block_identifier: BlockIdentifier) -> tuple[HexAddress, HexAddress, HexAddress, HexAddress, HexAddress]:
        """Read the modern Lagoon role addresses from ERC-7201 storage.

        The official v0.6 ``RolesStorage`` struct places the five addresses
        consumed by :class:`LagoonVaultInfo` in consecutive slots. The
        characterised v1 proxy has matching values at the fixed test block,
        but its unverified implementation is not assumed to be generally
        storage-compatible beyond these fields.

        :param block_identifier:
            Historical block at which to read the role namespace.
        :return:
            Whitelist manager, fee receiver, Safe, fee registry and valuation
            manager addresses in the same order as the legacy getter.
        """
        return (
            self._fetch_address_from_storage(LAGOON_MODERN_ROLES_STORAGE_SLOT, block_identifier),
            self._fetch_address_from_storage(LAGOON_MODERN_ROLES_STORAGE_SLOT + 1, block_identifier),
            self._fetch_address_from_storage(LAGOON_MODERN_ROLES_STORAGE_SLOT + 2, block_identifier),
            self._fetch_address_from_storage(LAGOON_MODERN_ROLES_STORAGE_SLOT + 3, block_identifier),
            self._fetch_address_from_storage(LAGOON_MODERN_ROLES_STORAGE_SLOT + 4, block_identifier),
        )

    def fetch_info(self) -> LagoonVaultInfo:
        """Use :py:meth:`info` property for cached access.

        :return:
            See :py:class:`LagoonVaultInfo`
        """
        vault_info = self.fetch_vault_info()
        safe_address = vault_info["safe"]

        safe_info_dict = {}
        # We have broken Lagoon contract on Arbitrum with 0x0 as Safe address
        if safe_address:
            try:
                safe = self.fetch_safe(safe_address)
                safe_info_dict = asdict(safe.retrieve_all_info())
                del safe_info_dict["address"]  # Key conflict
            except CannotRetrieveSafeInfoException as e:
                # Safe is not a safe but EOA address
                # https://arbiscan.io/address/0xb03EdA433d5bB1ef76b63087D4042A92C02822bD
                cause = getattr(e, "__cause__", None)
                logger.error(f"Lagoon Safe info fetch failed, exception {e} (cause: {cause}) for Safe {safe}, vault {self.vault_address}, vault info is {vault_info}", exc_info=cause)

        return vault_info | safe_info_dict

    @property
    def safe_address(self) -> HexAddress:
        """Get Safe multisig contract address"""
        return self.info["safe"]

    @cached_property
    def safe(self) -> Safe:
        """Get the underlying Safe object used as an API from safe-eth-py library.

        - Warps Safe Contract using Gnosis's in-house library
        """
        return self.fetch_safe(self.info["safe"])

    @cached_property
    def safe_contract(self) -> Contract:
        """Safe multisig as a contract.

        - Interact with Safe multisig ABI
        """
        return self.safe.contract

    @property
    def valuation_manager(self) -> HexAddress:
        """Valuation manager role on the vault."""
        return self.info["valuationManager"]

    @cached_property
    def silo_address(self) -> HexAddress:
        """Return the pending Silo contract address.

        Legacy vaults expose ``pendingSilo()`` directly. Versioned deployments
        use the storage slot documented by the official `v0.6 ERC7540Lib source
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/libraries/ERC7540Lib.sol>`__.
        The v1 path is restricted to the storage field characterised by the
        fixed-block production test.

        :return:
            Checksummed Silo contract address.
        """
        block_identifier = self._get_block_identifier()
        if self.version == LagoonVersion.legacy:
            return self.vault_contract.functions.pendingSilo().call(block_identifier=block_identifier)
        return self._fetch_address_from_storage(LAGOON_PENDING_SILO_STORAGE_SLOT, block_identifier)

    @cached_property
    def silo_contract(self) -> Contract:
        """Pending Silo contract.

        - This contract does not have any functionality, but stores deposits (pending USDC) and redemptions (pending share token)
        """
        return get_deployed_contract(self.web3, "lagoon/Silo.json", self.silo_address)

    def post_new_valuation(
        self,
        total_valuation: Decimal,
    ) -> ContractFunction:
        """Update the valuations of this vault.

        - Lagoon vault does not currently track individual positions, but takes a "total value" number

        - Updating this number also allows deposits and redemptions to proceed

        Notes:

            How can I post a valuation commitee update 1. as the valuationManager, call the function updateNewTotalAssets(_newTotalAssets) _newTotalAssets being expressed in underlying in its smallest unit for usdc, it would  with its 6 decimals. Do not take into account requestDeposit and requestRedeem in your valuation

            2. as the safe, call the function settleDeposit()

        :param total_valuation:
            The vault value nominated in :py:meth:`denomination_token`.

        :return:
            Bound contract function that can be turned to a transaction
        """
        logger.info("Updating vault %s valuation to %s %s", self.address, total_valuation, self.denomination_token.symbol)
        raw_amount = self.denomination_token.convert_to_raw(total_valuation)
        bound_func = self.vault_contract.functions.updateNewTotalAssets(raw_amount)
        return bound_func

    def settle_via_trading_strategy_module(self, valuation: Decimal | None = None, abi_version: str | None = None) -> ContractFunction:
        """Settle the new valuation and deposits.

        - settleDeposit will also settle the redeems request if possible. If there are enough assets in the safe it will settleRedeem
          It there are not enough assets, it will only settleDeposit.

        - if there is nothing to settle: no deposit and redeem requests you can still call settleDeposit/settleRedeem to validate the new nav

        - If there is not enough USDC to redeem, the transaction will revert

        :param abi_version:
            Use specific ABI version.

        :param raw_amount:
            Needed in Lagoon v0.5+
        """
        assert self.trading_strategy_module_address, "TradingStrategyModuleV0 not configured"
        if self.version != LagoonVersion.legacy:
            assert valuation is not None, "Lagoon v0.5.0+ needs valuation raw amount when calling settle"
            assert isinstance(valuation, Decimal), f"Expected DEcimal, got {type(valuation)}"
            raw_amount = self.denomination_token.convert_to_raw(valuation)
        else:
            raw_amount = None
        block = self.web3.eth.block_number
        pending = self.get_flow_manager().fetch_pending_deposit(block)
        logger.info(
            "Settling deposits for the block %d, we have %s %s deposits pending, raw mount is %s",
            block,
            pending,
            self.underlying_token.symbol,
            raw_amount,
        )
        if raw_amount is not None:
            bound_func = self.vault_contract.functions.settleDeposit(raw_amount)
        else:
            bound_func = self.vault_contract.functions.settleDeposit()
        return self.transact_via_trading_strategy_module(bound_func, abi_version=abi_version)

    def post_valuation_and_settle(
        self,
        valuation: Decimal,
        asset_manager: HexAddress,
        gas=1_000_000,
    ) -> HexBytes:
        """Do both new valuation and settle.

        - Quickhand method for asset_manager code

        - Only after this we can read back

        - Broadcasts two transactions and waits for the confirmation

        - If there is not enough USDC to redeem, the second transaction will fail with revert

        :return:
            The transaction hash of the settlement transaction
        """

        assert isinstance(valuation, Decimal)

        bound_func = self.post_new_valuation(valuation)
        tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
        assert_transaction_success_with_explanation(self.web3, tx_hash)

        if self.version == LagoonVersion.legacy:
            bound_func = self.settle_via_trading_strategy_module()
            tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)
        else:
            # New secure method safe for frontrunning
            logger.info("Settling new valuation using settleDeposit(_newTotalAssets), valuation is %s", valuation)
            bound_func = self.settle_via_trading_strategy_module(valuation)
            tx_hash = bound_func.transact({"from": asset_manager, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)

        return tx_hash

    def _fetch_fee_rates(self, block_identifier: BlockIdentifier) -> tuple[int, ...]:
        """Read the raw fee-rate tuple exposed by this Lagoon release.

        Legacy ABIs expose management and performance rates. The official
        `v0.6 vault source
        <https://github.com/hopperlabsxyz/lagoon-v0/blob/a8e73f5a5276aa4047b901083cbce127d7f7b470/src/v0.6.0/vault/Vault-v0.6.0.sol>`__
        adds entry, exit and synchronous-redemption haircut rates. The
        characterised v1 deployment returns the same five-field tuple at the
        fixed integration-test block.

        :param block_identifier:
            Historical block at which to read the fee rates.
        :return:
            ABI-specific tuple of basis-point fee rates.
        """
        return tuple(self.vault_contract.functions.feeRates().call(block_identifier=block_identifier))

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Get the Lagoon management fee as a fraction from zero to one.

        :param block_identifier:
            Historical block at which to read the fee rate.
        :return:
            Management fee fraction.
        """
        return self._fetch_fee_rates(block_identifier)[0] / LAGOON_FEE_RATE_DENOMINATOR

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Get the Lagoon performance fee as a fraction from zero to one.

        :param block_identifier:
            Historical block at which to read the fee rate.
        :return:
            Performance fee fraction.
        """
        return self._fetch_fee_rates(block_identifier)[1] / LAGOON_FEE_RATE_DENOMINATOR

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Get the modern Lagoon entry fee as a fraction from 0 to 1.

        Lagoon v0.6 returns the entry rate in basis points, and the fixed-block
        v1 deployment exposes the same field. Older versions do not expose it
        and retain the generic zero-fee behaviour.

        :param block_identifier:
            Historical block at which to read the fee rate.
        :return:
            Entry fee fraction.
        """
        if self.version not in LAGOON_MODERN_VERSIONS:
            return 0.0
        return self._fetch_fee_rates(block_identifier)[2] / LAGOON_FEE_RATE_DENOMINATOR

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Get the modern Lagoon exit fee as a fraction from 0 to 1.

        Lagoon v0.6 exposes a separate haircut rate for synchronous redemption,
        so it is intentionally not folded into this asynchronous exit fee. The
        fixed-block v1 deployment exposes the same two fields.

        :param block_identifier:
            Historical block at which to read the fee rate.
        :return:
            Exit fee fraction.
        """
        if self.version not in LAGOON_MODERN_VERSIONS:
            return 0.0
        return self._fetch_fee_rates(block_identifier)[3] / LAGOON_FEE_RATE_DENOMINATOR

    def get_fee_data(self) -> FeeData:
        """Read all Lagoon fee fields at the adapter's selected block.

        Lagoon exposes the generic fee fields in one ``feeRates()`` call. Read
        that tuple once so fixed-block scans cannot mix historical vault state
        with current fee state and ordinary scans avoid duplicate RPC calls.

        :return:
            Normalised management, performance, deposit and withdrawal fees.
        """
        block_identifier = self._get_block_identifier()
        rates = self._fetch_fee_rates(block_identifier)
        modern = self.version in LAGOON_MODERN_VERSIONS
        return FeeData(
            fee_mode=self.get_fee_mode(),
            management=rates[0] / LAGOON_FEE_RATE_DENOMINATOR,
            performance=rates[1] / LAGOON_FEE_RATE_DENOMINATOR,
            deposit=rates[2] / LAGOON_FEE_RATE_DENOMINATOR if modern else 0.0,
            withdraw=rates[3] / LAGOON_FEE_RATE_DENOMINATOR if modern else 0.0,
        )

    def has_custom_fees(self) -> bool:
        """Return whether Lagoon exposes a fee outside the generic fee model.

        The generic vault model has no field for Lagoon v0.6's synchronous
        redemption haircut. The characterised v1 deployment exposes the same
        fifth rate, so a non-zero value is reported as a custom fee.

        :return:
            ``True`` when the modern haircut rate is non-zero.
        """
        if self.version not in LAGOON_MODERN_VERSIONS:
            return False
        return self._fetch_fee_rates(self._get_block_identifier())[4] != 0

    def is_trading_strategy_module_enabled(self) -> bool:
        """Check if TradingStrategyModuleV0 is enabled on the Safe multisig."""
        assert self.trading_strategy_module_address, "TradingStrategyModuleV0 address must be separately given in the configuration"
        return bool(self.safe.contract.functions.isModuleEnabled(self.trading_strategy_module_address).call())

    def get_deposit_manager(self) -> "LagoonDepositManager":
        """Create Lagoon's ERC-7540 deposit manager.

        Lagoon uses the generic ERC-7540 lifecycle with protocol-specific
        access-policy checks and an Anvil settlement driver.

        :return:
            Lagoon-specific extension of the generic ERC-7540 manager.
        """
        # Imported here because the deposit manager imports :class:`LagoonVault`.
        from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager  # noqa: PLC0415

        return LagoonDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:  # noqa: PLR6301 - Implements the instance-level base API.
        """Declare Lagoon's asynchronously settleable manager lifecycle.

        Lagoon accepts the standard ERC-7540 request types and its selected
        manager can force-settle both ticket directions on Anvil.

        .. note::

            Trade-executor may call ``force_settle()`` for a full-lifecycle
            simulation only when this capability flag is true and it holds the
            matching ERC-7540 request ticket. Request-only behaviour remains
            unchanged.

        :return:
            Two-way asynchronous capability with Anvil settlement support.
        """
        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="asynchronous",
            redemption_flow="asynchronous",
            supports_anvil_settlement=True,
        )

    def can_check_deposit(self) -> bool:  # noqa: PLR6301 - Implements the instance-level base API.
        """Lagoon's maxDeposit does not work correctly for deposit availability checks."""
        return False

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002 - Signature follows the shared vault API.
        return f"https://app.lagoon.finance/vault/{self.chain_id}/{self.vault_address}"


class LagoonFlowManager(VaultFlowManager):
    """Read Lagoon's asynchronous deposit and redemption queues.

    Lagoon follows the `ERC-7540
    <https://eips.ethereum.org/EIPS/eip-7540>`__ request-settle-claim lifecycle.
    Pending deposits are the Silo's denomination-token balance and pending
    redemptions are its vault-share balance.
    """

    def __init__(self, vault: LagoonVault) -> None:
        self.vault = vault

    def fetch_pending_redemption(self, block_identifier: BlockIdentifier) -> Decimal:
        silo = self.vault.silo_contract
        return self.vault.share_token.fetch_balance_of(silo.address, block_identifier)

    def fetch_pending_deposit(self, block_identifier: BlockIdentifier) -> Decimal:
        silo = self.vault.silo_contract
        return self.vault.underlying_token.fetch_balance_of(silo.address, block_identifier)

    def fetch_pending_deposit_events(self, range: BlockRange) -> None:  # noqa: A002 - Signature follows the flow-manager API.
        raise NotImplementedError()

    def fetch_pending_redemption_event(self, range: BlockRange) -> None:  # noqa: A002 - Signature follows the flow-manager API.
        raise NotImplementedError()

    def fetch_processed_deposit_event(self, range: BlockRange) -> None:  # noqa: A002 - Signature follows the flow-manager API.
        pass

    def fetch_processed_redemption_event(self, vault: VaultSpec, range: BlockRange) -> None:  # noqa: A002 - Signature follows the flow-manager API.
        raise NotImplementedError()

    def calculate_underlying_needed_for_redemptions(self, block_identifier: BlockIdentifier) -> Decimal:
        """How much underlying token (USDC) we are going to need on the next redemption cycle.

        Computed as ``pendingRedemptionShares * sharePrice``.

        .. warning::

            This returns a **human-readable** :class:`decimal.Decimal`
            denomination amount, *not* a raw integer token amount. Callers must
            convert with ``denomination_token.convert_to_raw()`` before comparing
            against raw balances (e.g. the Anvil settlement top-up in
            :meth:`LagoonDepositManager._provision_safe_for_settlement` depends
            on this). Do not "fix" this to return raw units without updating
            those callers.

        :return:
            Human-readable decimal amount of the denomination token needed.
        """
        # How many shares we have pending for the redemption
        shares_pending = self.fetch_pending_redemption(block_identifier)
        share_price = self.vault.fetch_share_price(block_identifier)
        return shares_pending * share_price

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return Lagoon's legacy offchain settlement estimate.

        The structured :meth:`LagoonVault.get_withdrawal_period` accessor is
        the source of truth. This compatibility accessor retains the estimate
        for callers that still use the older flow-manager API.

        :return:
            Non-binding expected settlement cadence, or ``None`` when the
            curator has not supplied a valid estimate.
        """
        withdrawal_period = self.vault.get_withdrawal_period()
        return withdrawal_period.estimated_settlement if withdrawal_period else None
