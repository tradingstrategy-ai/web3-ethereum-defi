"""Abstraction over different deposit/redeem flows of vaults."""

import datetime
import enum
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat
from typing import Literal

from eth_typing import BlockIdentifier, BlockNumber, HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.provider.anvil import is_anvil
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.flow_events import PendingVaultFlow

logger = logging.getLogger(__name__)


VaultDepositFlow = Literal["synchronous", "asynchronous"]


class VaultDepositPermission(str, enum.Enum):
    """Vault-wide policy for accepting deposits.

    This class deliberately represents only whether the vault applies a
    whitelist policy.  It does not describe a particular account's balance,
    allowance, pause state, capacity, or whether an asynchronous request is
    currently claimable.

    The string values are persisted in vault metadata and public reports.
    """

    #: Deposits require protocol-specific account permission.
    whitelisted = "whitelisted"

    #: Any account may pass the protocol's permission policy.
    permissionless = "permissionless"

    #: The adapter cannot safely determine the vault-wide policy.
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class VaultDepositManagerCapability:
    """Static public integration capability of a vault deposit manager.

    This describes support implemented by :mod:`eth_defi`, rather than the
    vault's live cap, pause, allow-list, token balance, or liquidity state.
    A historical probe cannot establish future acceptance: consumers must make
    the request against current chain state and handle a current-state revert.

    :param can_deposit:
        Whether a complete public deposit lifecycle is implemented.
    :param can_redeem:
        Whether a complete public redemption lifecycle is implemented.
    :param deposit_flow:
        Request lifecycle for deposits when supported.
    :param redemption_flow:
        Request lifecycle for redemptions when supported.
    :param deposit_unsupported_reason:
        Stable adapter reason when deposits are deliberately unsupported.
    :param redemption_unsupported_reason:
        Stable adapter reason when redemptions are deliberately unsupported.
    :param supports_anvil_settlement:
        Whether the advertised asynchronous lifecycle can be advanced with its
        protocol-specific ticket on an Anvil fork. ``None`` means no
        asynchronous lifecycle is advertised.
    """

    can_deposit: bool
    can_redeem: bool
    deposit_flow: VaultDepositFlow | None = None
    redemption_flow: VaultDepositFlow | None = None
    deposit_unsupported_reason: str | None = None
    redemption_unsupported_reason: str | None = None
    supports_anvil_settlement: bool | None = None

    def __post_init__(self) -> None:
        """Validate that supported operations have a lifecycle declaration.

        :raises ValueError:
            If an operation flag and its flow declaration disagree.
        """
        if (self.deposit_flow is not None) != self.can_deposit:
            raise ValueError("deposit_flow must be present exactly when deposits are supported")
        if (self.redemption_flow is not None) != self.can_redeem:
            raise ValueError("redemption_flow must be present exactly when redemptions are supported")
        if self.can_deposit and self.deposit_unsupported_reason is not None:
            raise ValueError("deposit_unsupported_reason is valid only when deposits are unsupported")
        if self.can_redeem and self.redemption_unsupported_reason is not None:
            raise ValueError("redemption_unsupported_reason is valid only when redemptions are unsupported")
        if self.supports_anvil_settlement is not None and "asynchronous" not in (self.deposit_flow, self.redemption_flow):
            raise ValueError("supports_anvil_settlement requires an asynchronous lifecycle")

    def as_dict(self) -> dict[str, bool | str]:
        """Convert the capability to JSON-compatible primitives.

        :return:
            Directional capability object suitable for internal persistence.
        """
        result: dict[str, bool | str] = {
            "can_deposit": self.can_deposit,
            "can_redeem": self.can_redeem,
        }
        if self.deposit_flow is not None:
            result["deposit_flow"] = self.deposit_flow
        if self.redemption_flow is not None:
            result["redemption_flow"] = self.redemption_flow
        if self.deposit_unsupported_reason is not None:
            result["deposit_unsupported_reason"] = self.deposit_unsupported_reason
        if self.redemption_unsupported_reason is not None:
            result["redemption_unsupported_reason"] = self.redemption_unsupported_reason
        if self.supports_anvil_settlement is not None:
            result["supports_anvil_settlement"] = self.supports_anvil_settlement
        return result

    def as_initial_public_schema(self) -> dict[str, bool | str] | None:
        """Return the initial public schema or fail closed for partial support.

        The first export version advertises only symmetric manager support:
        both directions must either be implemented or explicitly unsupported.
        Keeping the internal representation directional leaves room for a
        future schema revision without making a partial manager look
        depositable today. An explicit ``False``/``False`` capability remains
        useful because it distinguishes a deliberate refusing manager from an
        adapter whose transaction support is unknown.

        :return:
            Symmetric public capability object, or ``None`` for partial support.
        """
        if self.can_deposit != self.can_redeem:
            return None
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class VaultRedemptionPreflight:
    """Amount-aware immediate-redemption guidance from a vault manager.

    The result is advisory because capacity can change before a transaction is
    mined. The manager's request constructor must repeat an available-capacity
    check before broadcasting. It is currently returned only by adapters that
    have an owner-specific immediate-capacity query, such as cSigma's
    `ERC-4626 maxRedeem <https://eips.ethereum.org/EIPS/eip-4626#maxredeem>`__.

    .. note::

        Trade-executor integrations must map an unavailable result, or a
        matching :class:`VaultFlowUnavailable` from request construction, to a
        capacity result before their generic receipt-analysis error handling.

    :param available:
        Whether the requested raw shares can be redeemed immediately.
    :param requested_raw_shares:
        Requested vault-share amount in native units.
    :param available_raw_shares:
        Current immediate capacity in native share units, when the adapter can
        determine it.
    :param reason:
        Stable adapter reason when the request is unavailable.
    """

    available: bool
    requested_raw_shares: int
    available_raw_shares: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate the available-capacity representation."""
        if self.requested_raw_shares < 0:
            raise ValueError("requested_raw_shares must not be negative")
        if self.available and self.reason is not None:
            raise ValueError("available preflight cannot have an unavailable reason")
        if not self.available and self.reason is None:
            raise ValueError("unavailable preflight must have a reason")


class AsyncVaultRequestStatus(enum.Enum):
    """Generic async vault request status.

    Protocol adapters map their internal status to these values.
    Used by the trade-executor settlement retry module to determine
    the next action for a pending vault trade, without importing
    protocol-specific code.
    """

    #: No request found for this ticket
    none = "none"

    #: Request submitted, awaiting settlement
    pending = "pending"

    #: Settlement done, claim available
    claimable = "claimable"

    #: Settlement failed, reclaim available to recover funds/shares
    reclaimable = "reclaimable"


class VaultFlowError(Exception):
    """Structured failure while preparing or executing a vault flow.

    Preflight failures use :class:`VaultFlowUnavailable`; mined transaction
    failures use :class:`VaultTransactionFailed`.  The common fields let a
    caller preserve useful context without treating a rejected request as a
    transaction that needs receipt handling.

    :param reason:
        Human-readable reason for the failed flow.
    :param protocol:
        Protocol adapter that detected the failure, when known.
    :param vault_address:
        Vault address whose flow was attempted, when known.
    :param caller:
        Address for which the flow was prepared, when known.
    :param direction:
        ``deposit`` or ``redeem`` when known.
    :param phase:
        Lifecycle phase such as ``request`` or ``transaction``.
    :param decoded_error:
        Protocol-specific decoded error name, when available.
    :param raw_revert_data:
        Raw revert payload, when available.
    :param requested_raw_amount:
        Requested amount in the contract's native raw unit, when applicable.
    :param available_raw_amount:
        Available amount in the contract's native raw unit, when applicable.
    :param function_selector:
        Four-byte selector of the denied protocol entry point, when known.
    :param error_selector:
        Four-byte selector of the expected or decoded custom error, when known.
    :param access_delay:
        Access-manager scheduling delay in seconds, when a caller is eligible
        only after delayed execution.
    """

    def __init__(
        self,
        reason: str,
        *,
        protocol: str | None = None,
        vault_address: HexAddress | None = None,
        caller: HexAddress | None = None,
        direction: Literal["deposit", "redeem"] | None = None,
        phase: str | None = None,
        decoded_error: str | None = None,
        raw_revert_data: HexBytes | None = None,
        requested_raw_amount: int | None = None,
        available_raw_amount: int | None = None,
        function_selector: HexBytes | None = None,
        error_selector: HexBytes | None = None,
        access_delay: int | None = None,
    ) -> None:
        """Store structured context for a vault-flow failure."""
        super().__init__(reason)
        self.reason = reason
        self.protocol = protocol
        self.vault_address = vault_address
        self.caller = caller
        self.direction = direction
        self.phase = phase
        self.decoded_error = decoded_error
        self.raw_revert_data = raw_revert_data
        self.requested_raw_amount = requested_raw_amount
        self.available_raw_amount = available_raw_amount
        self.function_selector = function_selector
        self.error_selector = error_selector
        self.access_delay = access_delay

    def __str__(self) -> str:
        """Format the failure reason with available flow context."""
        context = []
        if self.protocol:
            context.append(f"protocol={self.protocol}")
        if self.vault_address:
            context.append(f"vault={self.vault_address}")
        if self.caller:
            context.append(f"caller={self.caller}")
        if self.direction:
            context.append(f"direction={self.direction}")
        if self.phase:
            context.append(f"phase={self.phase}")
        if self.decoded_error:
            context.append(f"decoded_error={self.decoded_error}")
        if self.function_selector:
            context.append(f"function_selector={self.function_selector.hex()}")
        if self.error_selector:
            context.append(f"error_selector={self.error_selector.hex()}")
        if self.access_delay is not None:
            context.append(f"access_delay={self.access_delay}")
        if self.requested_raw_amount is not None:
            context.append(f"requested_raw_amount={self.requested_raw_amount}")
        if self.available_raw_amount is not None:
            context.append(f"available_raw_amount={self.available_raw_amount}")
        return f"{self.reason} ({', '.join(context)})" if context else self.reason


class VaultTransactionFailed(VaultFlowError):  # noqa: N818
    """One of vault deposit/redeem transactions reverted."""


class VaultFlowUnavailable(VaultFlowError):  # noqa: N818
    """A vault flow cannot be safely created before transaction broadcast."""


class UnsupportedVaultSimulation(RuntimeError):
    """A vault settlement simulation cannot safely run on this provider."""


@dataclass(slots=True)
class DepositRedeemEventFailure:
    """Structured failed-flow diagnostic returned by a vault manager."""

    tx_hash: HexBytes
    revert_reason: str | None

    #: Protocol adapter that analysed the failed flow, when available.
    protocol: str | None = None

    #: Vault whose lifecycle was being processed, when available.
    vault_address: HexAddress | None = None

    #: ``deposit`` or ``redeem`` when the manager knows the direction.
    direction: Literal["deposit", "redeem"] | None = None

    #: Lifecycle phase, such as ``request`` or ``claim``.
    phase: str | None = None

    #: Receipt status when it was available to the analyser.
    receipt_status: int | None = None


@dataclass(slots=True)
class DepositRedeemEventAnalysis:
    """Analyse a vault deposit/redeem event.

    - Done for the transaction where we get our assets into our wallet,
      so we can determine the actualy executed price of shares we received/sold
    """

    from_: HexAddress
    to: HexAddress
    denomination_amount: Decimal
    share_count: Decimal
    tx_hash: HexBytes
    block_number: BlockNumber
    block_timestamp: datetime.datetime

    def __post_init__(self):
        assert self.denomination_amount > 0
        assert self.share_count > 0

    def is_success(self):
        return self.revert_reason is None

    def get_share_price(self) -> Decimal:
        return self.denomination_amount / self.share_count


@dataclass(slots=True)
class DepositTicket:
    """In-progress deposit request.

    - `Needed for ERC-7540 <https://tradingstrategy.ai/glossary/erc-7540>`__
    """

    vault_address: HexAddress
    owner: HexAddress
    to: HexAddress
    raw_amount: int

    #: Last of transaction hashes
    tx_hash: HexBytes
    gas_used: int

    #: Last tx block number
    block_number: int

    #: Last tx block timestamp
    block_timestamp: datetime.datetime

    def __post_init__(self):
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_amount) == int, f"Got {type(self.raw_amount)}: {self.raw_amount}"
        assert isinstance(self.tx_hash, HexBytes), f"Got {type(self.tx_hash)}: {self.tx_hash}"


@dataclass(slots=True)
class RedemptionTicket:
    """In-progress redemption request.

    - Needs to wait until the epoch time is over or owner has settled
    - Serialisable class
    """

    vault_address: HexAddress
    owner: HexAddress
    to: HexAddress
    raw_shares: int
    tx_hash: HexBytes

    def __post_init__(self):
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_shares) == int, f"Got {type(self.raw_shares)}: {self.raw_shares}"
        assert isinstance(self.tx_hash, HexBytes), f"Got {type(self.tx_hash)}: {self.tx_hash}"

    @abstractmethod
    def get_request_id(self) -> int:
        """Get the redemption request id.

        - If vault uses some sort of request ids to track the withdrawals
        - Needed for settlement
        """
        raise NotImplementedError()


@dataclass(frozen=True, slots=True)
class VaultForcedSettlementResult:
    """Outcome of an Anvil-only forced settlement attempt.

    Synchronous managers return a no-op result because their successful
    request transaction already completes the lifecycle. Asynchronous managers
    return the ticket status before and after their protocol-specific
    settlement transaction(s).
    """

    #: Ticket progressed by the simulation, or None for synchronous flows.
    ticket: DepositTicket | RedemptionTicket | None

    #: False when the completed flow does not need a settlement transaction.
    settlement_required: bool

    #: Request status before settlement, when a ticket was supplied.
    status_before: AsyncVaultRequestStatus | None

    #: Request status after settlement, when a ticket was supplied.
    status_after: AsyncVaultRequestStatus | None

    #: Transactions broadcast by the forced settlement helper.
    transaction_hashes: tuple[HexBytes, ...] = ()


class CannotParseRedemptionTransaction(Exception):
    """We did no know how our redemption transaction went."""


@dataclass(slots=True)
class RedemptionRequest:
    """Wrap the different redeem functions async vaults implement."""

    #: Vault we are dealing with
    vault: "VaultBase"

    #: Owner of the shares
    owner: HexAddress

    #: Receiver of underlying asset
    to: HexAddress

    #: Human-readable shares
    shares: Decimal

    #: Raw amount of shares
    raw_shares: int

    #: Transactions we need to perform in order to open a redemption
    #:
    #: It's a list because for Gains we need 2 tx
    funcs: list[ContractFunction]

    def __post_init__(self):
        from eth_defi.vault.base import VaultBase

        assert isinstance(self.vault, VaultBase), f"Got {type(self.vault)}"
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert type(self.raw_shares) == int, f"Got {type(self.raw_shares)}"
        assert self.raw_shares > 0

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> RedemptionTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction

        :raise CannotParseRedemptionTransaction:
            If we did not know how to parse the transaction
        """
        return RedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=self.raw_shares,
            tx_hash=tx_hashes[-1],
        )

    def broadcast(self, from_: HexAddress = None, gas: int = 1_000_000) -> list[HexBytes]:
        """Broadcast all the transactions in this request.

        :param from_:
            Address to send the transactions from

        :param gas:
            Gas limit to use for each transaction

        :return:
            List of transaction hashes
        """

        if from_ is None:
            from_ = self.owner

        tx_hashes = []
        for func in self.funcs:
            tx_hash = func.transact({"from": from_, "gas": gas})
            assert_transaction_success_with_explanation(self.web3, tx_hash)
            tx_hashes.append(tx_hash)
        return self.parse_redeem_transaction(tx_hashes)


@dataclass(slots=True)
class DepositRequest:
    """Wrap the different deposit functions async vaults implement."""

    #: Vault we are dealing with
    vault: "VaultBase"

    #: Owner of the shares
    owner: HexAddress

    #: Receiver of underlying asset
    to: HexAddress

    #: Human-readable shares
    amount: Decimal

    #: Raw amount of shares
    raw_amount: int

    #: Transactions we need to perform in order to open a redemption
    #:
    #: It's a list because for Gains we need 2 tx
    funcs: list[ContractFunction]

    #: Set transaction gas limit
    gas: int | None = None

    #: Attached ETH value to the tx
    value: Decimal | None = None

    def __post_init__(self):
        from eth_defi.vault.base import VaultBase

        assert isinstance(self.vault, VaultBase), f"Got {type(self.vault)}"
        assert self.owner.startswith("0x"), f"Got {self.owner}"
        assert self.to.startswith("0x"), f"Got {self.to}"
        assert self.raw_amount > 0
        assert type(self.raw_amount) == int, f"Got {type(self.raw_amount)}"

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def parse_deposit_transaction(
        self,
        tx_hashes: list[HexBytes],
    ) -> DepositTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction

        - Most throw an

        :raise CannotParseRedemptionTransaction:
            If we did not know how to parse the transaction

        :raise VaultTransactionFailed:
            One of transactions reverted
        """

        gas_used = 0

        for tx_hash in tx_hashes:
            tx = self.web3.eth.get_transaction(tx_hash)
            receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            assert receipt is not None, f"Transaction was not yet mined: {tx_hash}"
            if receipt["status"] != 1:
                raise VaultTransactionFailed(f"Vault {self.vault} tranasaction {tx_hash} failed {receipt}")
            gas_used += receipt["gasUsed"]
            block_number = tx["blockNumber"]

        block_timestamp = get_block_timestamp(self.web3, block_number)

        return DepositTicket(vault_address=self.vault.address, owner=self.owner, to=self.to, raw_amount=self.raw_amount, tx_hash=tx_hash, gas_used=gas_used, block_timestamp=block_timestamp, block_number=block_number)

    def broadcast(self, from_: HexAddress = None, gas: int | None = None, check_value=True) -> RedemptionTicket:
        """Broadcast all the transactions in this request.

        :param from_:
            Address to send the transactions from

        :param gas:
            Gas limit to use for each transaction

        :return:
            List of transaction hashes

        :raise TransactionAssertionError:
            If any of the transactions revert
        """

        if from_ is None:
            from_ = self.owner

        if gas is None:
            if self.gas:
                gas = self.gas
            else:
                # Default to 1M
                gas = 1_000_000

        tx_data = {"from": from_, "gas": gas}
        if self.value:
            tx_data["value"] = Web3.to_wei(self.value, "ether")

            # If we ask for value, make sure our account is topped up
            if check_value:
                balance = self.web3.eth.get_balance(from_)
                assert balance >= tx_data["value"], f"Not enough ETH balance in {from_} to cover value {self.value} ETH, has {Web3.from_wei(balance, 'ether')} ETH"

        logger.info(
            "Broadcasting deposit request to vault %s from %s with gas %s and tx params:\n%s",
            self.vault.address,
            from_,
            gas,
            pformat(tx_data),
        )

        tx_hashes = []
        for func in self.funcs:
            tx_hash = func.transact(tx_data)

            assert_transaction_success_with_explanation(self.web3, tx_hash)
            tx_hashes.append(tx_hash)

        return self.parse_deposit_transaction(tx_hashes)


class VaultDepositManager(ABC):
    """Abstraction over different deposit/redeem flows of vaults.

    Supported simulation path: every manager exposes force_settle() for
    Anvil-based integration tests. Synchronous managers use its no-op
    implementation; asynchronous managers override it when their selected
    test lifecycle needs settlement.

    Known limitations: the common interface cannot infer protocol-specific
    settlement roles, valuations or queues. Each protocol manager documents
    the concrete asynchronous path it supports and raises
    UnsupportedVaultSimulation when it has no safe Anvil driver.
    """

    def __init__(
        self,
        vault: "eth_defi.vault.base.VaultBase",
    ):
        self.vault = vault

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
    ) -> VaultForcedSettlementResult:
        """Force the selected ticket forward on an Anvil simulation.

        Synchronous managers do not require settlement and return a no-op
        result when called with None. Asynchronous managers must override this
        method and supply their request ticket.

        :param ticket:
            Pending async request ticket, or None for a synchronous flow.
        :return:
            Settlement outcome with before/after status and transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil or an async manager lacks a driver.
        """
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(f"{self.__class__.__name__}.force_settle() requires an Anvil provider")

        if ticket is None and (self.has_synchronous_deposit() or self.has_synchronous_redemption()):
            return VaultForcedSettlementResult(
                ticket=None,
                settlement_required=False,
                status_before=None,
                status_after=None,
            )

        raise UnsupportedVaultSimulation(f"{self.__class__.__name__} has no Anvil settlement driver for {type(ticket).__name__}")

    @abstractmethod
    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """

    @abstractmethod
    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """

    @abstractmethod
    def estimate_deposit(self, owner: HexAddress | None, amount: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """How many shares we get for a deposit."""

    @abstractmethod
    def estimate_redeem(self, owner: HexAddress | None, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """How many denomination tokens we get for a redeem."""

    @abstractmethod
    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> DepositRequest:
        pass

    @abstractmethod
    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> RedemptionRequest:
        """Create a redemption request.

        Abstracts IPOR, Lagoon, Gains, other vault redemption flow.

        See :py:class:`eth_defi.gains.vault.GainsVault` for an example usage.

        Flow

        1. create_redemption_request
        2. sign and broadcast the transaction
        3. parse success and redemption request id from the transaction
        4. wait until the redemption delay is over
        5. settle the redemption request

        :param owner:
            Deposit owner.

        :param shares:
            Share amount in decimal.

            Will be converted to `raw_shares` using `share_token` decimals.

        :param raw_shares:
            Raw amount in share token

        :return:
            Redemption request wrapper.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement create_redemption_request()")

    @abstractmethod
    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check if the owner has an active redemption request.

        :param owner:
            Owner of the shares

        :return:
            True if there is an active redemption request
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement is_redemption_in_proges()")

    @abstractmethod
    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check if the owner has an active deposit request.

        :param owner:
            Owner of the shares

        :return:
            True if there is an active redemption request
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement is_redemption_in_proges()")

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Can we start depositing now.

        Vault can be full?
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_deposit_request()")

    def get_deposit_approval_target(self) -> HexAddress:
        """Return the ERC-20 spender required for a deposit request.

        Standard ERC-4626 and the currently supported async adapters pull
        denomination tokens from the vault address itself.  An adapter using a
        different router or silo must override this method; guarded callers use
        it to whitelist and validate the exact approval calldata.

        :return:
            ERC-20 approval spender address.
        """
        return self.vault.address

    def fetch_vault_flow_events(
        self,
        hypersync_client,
        start_block: int,
        end_block: int,
    ) -> Iterator[PendingVaultFlow]:
        """Fetch asynchronous vault request events from an indexed backend.

        The base implementation returns no events for vault managers that do
        not have a two-phase deposit or redemption flow.

        :param hypersync_client:
            Configured Hypersync client for this vault's chain.

        :param start_block:
            Inclusive start block.

        :param end_block:
            Inclusive end block.

        :return:
            Iterator of protocol-neutral pending vault flow events.
        """
        return iter(())

    def get_max_deposit(self, owner: HexAddress) -> Decimal | None:
        """How much we can deposit"""
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_redemption_request()")

    @abstractmethod
    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Gains allows request redepetion only two first days of three days epoch.

        :return:
            True if can create a redemption request now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_create_redemption_request()")

    @abstractmethod
    def can_finish_redeem(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> bool:
        """Check if the redemption request can be redeemed now.

        - Phase 2 of redemption, after settlement

        :param redemption_ticket:
            Redemption redemption_ticket ticket from `create_redemption_request()`

        :return:
            True if can be redeemed now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_redeem()")

    @abstractmethod
    def can_finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> bool:
        """Can we finish the deposit process in async reposits"""
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_deposit()")

    @abstractmethod
    def finish_deposit(
        self,
        deposit_ticket: DepositTicket,
    ) -> ContractFunction:
        """Can we finish the deposit process in async vault.

        - We can claim our shares from the vault now
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement can_deposit()")

    @abstractmethod
    def finish_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction | None:
        """Build the depositor-owned final redemption transaction when one exists.

        Some asynchronous vaults, such as Ember, transfer funds directly from
        an operator transaction. They deliberately return ``None`` here: an
        asset manager must not attempt to invoke an operator-only settlement
        method on behalf of its depositor.

        :param redemption_ticket:
            Persisted asynchronous redemption request.
        :return:
            Bound depositor claim call, or ``None`` when the protocol has no
            depositor-owned finish action.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement settle_redemption()")

    @abstractmethod
    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Get the redemption delay for this vault.

        - What is overall redemption delay: not related to the current moment

        - How long it takes before a redemption request is allowed

        - This is not specific for any address, but the general vault rule

        - E.g. you get  0xa592703b is an IPOR Fusion error code AccountIsLocked,
          if you `try to instantly redeem from IPOR vaults <https://ethereum.stackexchange.com/questions/170119/is-there-a-way-to-map-binary-solidity-custom-errors-to-their-symbolic-sources>`__

        :return:
            Redemption delay as a :py:class:`datetime.timedelta`

        :raises NotImplementedError:
            If not implemented for this vault protocoll.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_redemption_delay()")

    @abstractmethod
    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Get the redemption timer left for an address.

        - How long it takes before a redemption request is allowed

        - This is not specific for any address, but the general vault rule

        - E.g. you get  0xa592703b is an IPOR Fusion error code AccountIsLocked,
          if you `try to instantly redeem from IPOR vaults <https://ethereum.stackexchange.com/questions/170119/is-there-a-way-to-map-binary-solidity-custom-errors-to-their-symbolic-sources>`__

        :return:
            UTC timestamp when the account can redeem.

            Naive datetime, or ``None`` when the protocol has no deterministic
            on-chain deadline.

        :raises NotImplementedError:
            If not implemented for this vault protocoll.
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_redemption_delay_over()")

    def fetch_completed_redemption_tx_hash(
        self,
        ticket: RedemptionTicket,
    ) -> HexBytes | None:
        """Find an operator-owned terminal redemption transaction when available.

        Claim-based protocols finish through :meth:`finish_redemption` and do
        not need this lookup. Operator-finalised protocols override the hook to
        find and validate the transaction that paid the requested receiver.

        :param ticket:
            Persisted redemption request to locate.
        :return:
            Terminal transaction hash, or ``None`` if the protocol has not
            observed one yet.
        """
        return None

    def get_deposit_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Estimate when a pending async deposit request will settle.

        - Mirror of :py:meth:`get_redemption_delay_over` for the deposit side.

        - Used to show an estimated settlement time for unsettled deposits
          (e.g. in the trade-executor ``trade-ui`` table).

        - Default returns ``None``: the protocol has no deterministic on-chain
          settlement schedule (e.g. operator-driven ERC-7540 vaults like Lagoon).
          Subclasses with a predictable settlement cadence (e.g. Ostium V1.5)
          override this to return an estimated UTC timestamp.

        :param address:
            Owner of the pending deposit request.

        :return:
            Naive UTC timestamp when the deposit is expected to settle, or
            ``None`` when no on-chain estimate is available.
        """
        return None

    @abstractmethod
    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse the transaction where we claim shares

        - Return information of the actual executed price for which we got the shares for
        """

    @abstractmethod
    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse the transaction where we claim our capital back.

        - Return information of the actual executed price for which we got the shares for
        """

    # --- Async vault lifecycle: ticket serialisation ---

    def serialize_deposit_ticket(self, ticket: DepositTicket) -> dict:
        """Serialise a deposit ticket to a dict for persistence.

        The trade-executor stores this in ``trade.other_data`` so that
        the settlement retry module can reconstruct the ticket after a
        process restart.

        Default implementation stores base :py:class:`DepositTicket` fields.
        Subclasses override to add protocol-specific fields
        (e.g. ``settlement_id`` for Ostium, ``requestId`` for ERC-7540).
        """
        return {
            "vault_address": ticket.vault_address,
            "vault_owner": ticket.owner,
            "vault_to": ticket.to,
            # Stored as a string: 18-decimal raw amounts exceed the JavaScript
            # safe-integer limit that the trade-executor state file enforces.
            "vault_raw_amount": str(ticket.raw_amount),
            "vault_request_tx_hash": ticket.tx_hash.hex(),
            "vault_request_gas_used": ticket.gas_used,
            "vault_request_block_number": ticket.block_number,
            "vault_request_block_timestamp": ticket.block_timestamp.isoformat() if ticket.block_timestamp else None,
        }

    def reconstruct_deposit_ticket(self, data: dict) -> DepositTicket:
        """Reconstruct a deposit ticket from a serialised dict.

        Default returns a base :py:class:`DepositTicket`.
        Subclasses override for protocol-specific ticket types.
        """
        ts = data.get("vault_request_block_timestamp")
        return DepositTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            # int() accepts both the current string form and legacy int form
            raw_amount=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            gas_used=data.get("vault_request_gas_used", 0),
            block_number=data.get("vault_request_block_number", 0),
            block_timestamp=datetime.datetime.fromisoformat(ts) if ts else None,
        )

    def serialize_redemption_ticket(self, ticket: RedemptionTicket) -> dict:
        """Serialise a redemption ticket to a dict for persistence.

        Default implementation stores base :py:class:`RedemptionTicket` fields.
        Subclasses override to add protocol-specific fields.
        """
        return {
            "vault_address": ticket.vault_address,
            "vault_owner": ticket.owner,
            "vault_to": ticket.to,
            # Stored as a string: 18-decimal raw share amounts exceed the JavaScript
            # safe-integer limit that the trade-executor state file enforces.
            "vault_raw_amount": str(ticket.raw_shares),
            "vault_request_tx_hash": ticket.tx_hash.hex(),
        }

    def reconstruct_redemption_ticket(self, data: dict) -> RedemptionTicket:
        """Reconstruct a redemption ticket from a serialised dict.

        Async vault managers **must** override this to return their
        protocol-specific ticket subclass. The base implementation
        raises :py:class:`NotImplementedError` because
        :py:class:`RedemptionTicket` has abstract methods.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must override reconstruct_redemption_ticket() for async vault support")

    # --- Async vault lifecycle: settlement status ---

    def get_deposit_request_status(
        self,
        ticket: DepositTicket,
    ) -> AsyncVaultRequestStatus:
        """Query the current status of an async deposit request.

        Default implementation probes via :py:meth:`can_finish_deposit`.
        Subclasses should override for more accurate status reporting
        (e.g. distinguishing ``reclaimable`` from ``pending``).
        """
        if self.can_finish_deposit(ticket):
            return AsyncVaultRequestStatus.claimable
        return AsyncVaultRequestStatus.pending

    def get_redemption_request_status(
        self,
        ticket: RedemptionTicket,
    ) -> AsyncVaultRequestStatus:
        """Query the current status of an async redemption request.

        Default implementation probes via :py:meth:`can_finish_redeem`.
        Subclasses should override for more accurate status reporting.
        """
        if self.can_finish_redeem(ticket):
            return AsyncVaultRequestStatus.claimable
        return AsyncVaultRequestStatus.pending

    # --- Async vault lifecycle: reclaim after failed settlement ---

    def reclaim_deposit(
        self,
        ticket: DepositTicket,
    ) -> ContractFunction | None:
        """Return a function to recover funds after a failed async deposit settlement.

        Returns ``None`` if the protocol does not support reclaim.
        """
        return None

    def reclaim_withdrawal(
        self,
        ticket: RedemptionTicket,
    ) -> ContractFunction | None:
        """Return a function to recover shares after a failed async withdrawal settlement.

        Returns ``None`` if the protocol does not support reclaim.
        """
        return None
