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

from eth_typing import BlockIdentifier, BlockNumber, HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.provider.anvil import is_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.flow_events import PendingVaultFlow

logger = logging.getLogger(__name__)


VaultDepositFlow = Literal["synchronous", "asynchronous"]

#: Export caveat for classifications made without inspecting optional
#: protocol-specific permission hooks.
PERMISSIONED_HOOK_CHECKS_NOT_PERFORMED_NOTE = "No permissioned hook checks were performed"


class VaultDepositPermission(str, enum.Enum):
    """Whether deposits require KYC or comparable identity approval.

    This class deliberately represents only a depositor's KYC or manual
    identity-approval requirement. It does not describe a particular account's
    balance, allowance, token-holding eligibility, pause state, capacity,
    lock-up, open date, epoch window, or whether an asynchronous request is
    currently claimable.

    The string values are persisted in vault metadata and public reports.
    """

    #: Deposits require prior KYC or comparable manual identity approval.
    whitelisted = "whitelisted"

    #: Deposits need no prior KYC or manual identity approval.
    #:
    #: An open date, lock-up, epoch, cap, pause or token-holding condition does
    #: not change this status.
    permissionless = "permissionless"

    #: The adapter cannot safely determine whether KYC is required.
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
        settlement assertion is needed for the selected synchronous direction.
        ``False`` publishes an advertised asynchronous lifecycle which cannot
        be safely advanced on Anvil.
    :param anvil_settlement_unsupported_reason:
        Stable concrete reason why an advertised asynchronous lifecycle cannot
        be advanced on Anvil. Required when
        ``supports_anvil_settlement=False`` and forbidden otherwise.
    """

    can_deposit: bool
    can_redeem: bool
    deposit_flow: VaultDepositFlow | None = None
    redemption_flow: VaultDepositFlow | None = None
    deposit_unsupported_reason: str | None = None
    redemption_unsupported_reason: str | None = None
    supports_anvil_settlement: bool | None = None
    anvil_settlement_unsupported_reason: str | None = None

    #: Accepted token addresses for an explicit multi-asset deposit flow.
    deposit_assets: tuple[HexAddress, ...] = ()

    #: Allow the initial public schema to expose only one flow direction.
    publish_partial: bool = False

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
        if self.supports_anvil_settlement is False and not self.anvil_settlement_unsupported_reason:
            raise ValueError("supports_anvil_settlement=False requires anvil_settlement_unsupported_reason")
        if self.supports_anvil_settlement is not False and self.anvil_settlement_unsupported_reason is not None:
            raise ValueError("anvil_settlement_unsupported_reason is valid only when supports_anvil_settlement=False")
        if self.deposit_assets and not self.can_deposit:
            raise ValueError("deposit_assets requires deposit support")

    def as_dict(self) -> dict[str, bool | str | list[HexAddress]]:
        """Convert the capability to JSON-compatible primitives.

        :return:
            Directional capability object suitable for internal persistence.
        """
        result: dict[str, bool | str | list[HexAddress]] = {
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
        if self.anvil_settlement_unsupported_reason is not None:
            result["anvil_settlement_unsupported_reason"] = self.anvil_settlement_unsupported_reason
        if self.deposit_assets:
            result["deposit_assets"] = list(self.deposit_assets)
        return result

    def as_initial_public_schema(self) -> dict[str, bool | str | list[HexAddress]] | None:
        """Return the initial public capability schema.

        Existing partial adapters remain fail-closed unless they explicitly
        opt in after their supported direction has been fork-proven.

        :return:
            JSON-compatible capability object, or ``None`` for an unpublished
            partial adapter.
        """
        if self.can_deposit != self.can_redeem and not self.publish_partial:
            return None
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class VaultRedemptionPreflight:
    """Amount-aware immediate-redemption guidance from a vault manager.

    The result is advisory because capacity can change before a transaction is
    mined. The manager's request constructor must repeat an available-capacity
    check before broadcasting. It is currently returned only by adapters that
    can determine an immediate full-fill capacity from their authoritative
    protocol state, such as cSigma's queue-adjusted reserve check.

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
    :param asset_address:
        Input asset selected for a multi-asset flow, when known.
    :param direction:
        ``deposit`` or ``redeem`` when known.
    :param phase:
        Lifecycle phase such as ``request`` or ``transaction``.
    :param decoded_error:
        Protocol-specific decoded error name, when available.
    :param preflight_result:
        Stable consumer-facing result for a predictable refusal.  This is an
        optional migration field: callers must fall back to ``decoded_error``
        while supporting older eth-defi releases that do not populate it.
    :param raw_revert_data:
        Raw revert payload, when available.
    :param requested_raw_amount:
        Requested amount in the contract's native raw unit, when applicable.
    :param available_raw_amount:
        Available amount in the contract's native raw unit, when applicable.
    :param minimum_raw_amount:
        Protocol minimum in the contract's native raw unit, when applicable.
    :param function_selector:
        Four-byte selector of the denied protocol entry point, when known.
    :param error_selector:
        Four-byte selector of the expected or decoded custom error, when known.
    :param access_delay:
        Access-manager scheduling delay in seconds, when a caller is eligible
        only after delayed execution.
    :param next_open:
        Naive UTC time at which a predictable closed protocol window next opens.
    """

    def __init__(
        self,
        reason: str,
        *,
        protocol: str | None = None,
        vault_address: HexAddress | None = None,
        caller: HexAddress | None = None,
        asset_address: HexAddress | None = None,
        direction: Literal["deposit", "redeem"] | None = None,
        phase: str | None = None,
        decoded_error: str | None = None,
        preflight_result: str | None = None,
        raw_revert_data: HexBytes | None = None,
        requested_raw_amount: int | None = None,
        available_raw_amount: int | None = None,
        minimum_raw_amount: int | None = None,
        function_selector: HexBytes | None = None,
        error_selector: HexBytes | None = None,
        access_delay: int | None = None,
        next_open: datetime.datetime | None = None,
    ) -> None:
        """Store structured context for a vault-flow failure."""
        super().__init__(reason)
        self.reason = reason
        self.protocol = protocol
        self.vault_address = vault_address
        self.caller = caller
        self.asset_address = asset_address
        self.direction = direction
        self.phase = phase
        self.decoded_error = decoded_error
        self.preflight_result = preflight_result
        self.raw_revert_data = raw_revert_data
        self.requested_raw_amount = requested_raw_amount
        self.available_raw_amount = available_raw_amount
        self.minimum_raw_amount = minimum_raw_amount
        self.function_selector = function_selector
        self.error_selector = error_selector
        self.access_delay = access_delay
        self.next_open = next_open

    def __str__(self) -> str:
        """Format the failure reason with available flow context."""
        context = []
        if self.protocol:
            context.append(f"protocol={self.protocol}")
        if self.vault_address:
            context.append(f"vault={self.vault_address}")
        if self.caller:
            context.append(f"caller={self.caller}")
        if self.asset_address:
            context.append(f"asset={self.asset_address}")
        if self.direction:
            context.append(f"direction={self.direction}")
        if self.phase:
            context.append(f"phase={self.phase}")
        if self.decoded_error:
            context.append(f"decoded_error={self.decoded_error}")
        if self.preflight_result:
            context.append(f"preflight_result={self.preflight_result}")
        if self.function_selector:
            context.append(f"function_selector={self.function_selector.hex()}")
        if self.error_selector:
            context.append(f"error_selector={self.error_selector.hex()}")
        if self.access_delay is not None:
            context.append(f"access_delay={self.access_delay}")
        if self.next_open is not None:
            context.append(f"next_open={self.next_open.isoformat()}")
        if self.requested_raw_amount is not None:
            context.append(f"requested_raw_amount={self.requested_raw_amount}")
        if self.available_raw_amount is not None:
            context.append(f"available_raw_amount={self.available_raw_amount}")
        if self.minimum_raw_amount is not None:
            context.append(f"minimum_raw_amount={self.minimum_raw_amount}")
        return f"{self.reason} ({', '.join(context)})" if context else self.reason


def extract_revert_data(error: BaseException) -> HexBytes | None:
    """Extract raw EVM revert data from common Web3 exception shapes.

    :param error:
        Web3 exception raised by ``eth_call``.
    :return:
        Raw revert payload when exposed by the provider.
    """
    candidates = [getattr(error, "data", None)]
    if error.args:
        candidates.append(error.args[0])

    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("data")
        if isinstance(candidate, (bytes, str)):
            try:
                return HexBytes(candidate)
            except ValueError:
                continue
    return None


class VaultTransactionFailed(VaultFlowError):  # noqa: N818
    """One of vault deposit/redeem transactions reverted."""


class VaultFlowUnavailable(VaultFlowError):  # noqa: N818
    """A vault flow cannot be safely created before transaction broadcast."""


class WhitelistingRequired(VaultFlowUnavailable):  # noqa: N818
    """A deposit was attempted for a vault whose whitelist excludes the owner.

    Raised during deposit preflight when a vault applies a deposit whitelist
    (permissioned) policy, that policy is applicable and queryable, and the
    depositing account is not a member of it. The account must be whitelisted
    by the vault curator before a deposit can succeed.

    This is a subclass of :class:`VaultFlowUnavailable` so existing callers
    that catch the base preflight failure keep working, while callers that
    want to react specifically to a missing whitelist entry — for example to
    surface a "whitelisting required" state instead of a generic failure —
    can catch this narrower type.

    Message contract for diagnostics: whenever this exception is raised, its
    ``reason`` message (the first positional argument, i.e. the text returned
    by ``str(exc)`` before the structured context) **must** identify the
    failing deposit unambiguously by including all three of:

    - the **chain id** the vault is deployed on
      (:meth:`~eth_defi.vault.base.VaultBase.chain_id`),
    - the **vault contract address**
      (:meth:`~eth_defi.vault.base.VaultBase.address`), and
    - the **depositor address** that was denied (the request owner/caller).

    These three values must be embedded in the message string itself — not
    only passed through the ``vault_address`` and ``caller`` structured
    fields — so a single logged message line is self-describing for
    diagnostics without the reader having to reconstruct the failing vault or
    account from surrounding context. Include the structured ``vault_address``
    and ``caller`` fields as well.
    :meth:`VaultDepositManager.check_deposit_whitelist` produces a compliant
    message; adapters that raise this directly must do the same.

    Contract for adapter authors: every deposit manager must raise this from
    its deposit preflight when the vault's whitelist policy can be determined,
    is applicable, and the owner is not permitted. Use
    :meth:`VaultDepositManager.check_deposit_whitelist` to satisfy the
    contract. When the policy cannot be determined (the adapter's whitelist
    reads raise :class:`NotImplementedError`) this exception must not be
    raised; the deposit either proceeds and surfaces any genuine denial as an
    onchain revert, or a protocol adapter may fail closed with a plain
    :class:`VaultFlowUnavailable` if unknown admission is unsafe.
    """


class UnsupportedVaultSimulation(RuntimeError):
    """A vault settlement simulation cannot safely run on this provider.

    :param reason:
        Human-readable explanation of why the simulation cannot run.
    :param unsupported_reason:
        Stable, machine-readable adapter reason.  This is retained separately
        from ``reason`` so consumers never need to parse exception prose.
    :param protocol:
        Protocol adapter that rejected settlement, when known.
    :param vault_address:
        Vault whose settlement was rejected, when known.
    :param direction:
        ``deposit`` or ``redeem`` for the rejected asynchronous ticket.
    :param phase:
        Lifecycle phase that was rejected. Defaults to ``settlement``.
    """

    def __init__(
        self,
        reason: str,
        *,
        unsupported_reason: str | None = None,
        protocol: str | None = None,
        vault_address: HexAddress | None = None,
        direction: Literal["deposit", "redeem"] | None = None,
        phase: str = "settlement",
    ) -> None:
        """Store a machine-readable settlement refusal and its vault context.

        :param reason:
            Human-readable explanation of the refusal.
        :param unsupported_reason:
            Stable adapter reason for consumer result mapping.
        :param protocol:
            Protocol adapter that rejected settlement, when known.
        :param vault_address:
            Vault whose settlement was rejected, when known.
        :param direction:
            Rejected asynchronous ticket direction, when known.
        :param phase:
            Rejected lifecycle phase.
        """
        super().__init__(reason)
        self.reason = reason
        self.unsupported_reason = unsupported_reason
        self.protocol = protocol
        self.vault_address = vault_address
        self.direction = direction
        self.phase = phase


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
class VaultDirectPayoutEvidence:
    """Evidence for an asynchronous request settled by direct token payment.

    Some protocols consume a request and pay the receiver without exposing an
    intermediate claimable state.  Such a transition is terminal only when
    the request-specific event and the receiver's denomination-token balance
    agree.  This intentionally contains no protocol-specific event payload so
    consumers can apply the same terminal-result rule to every adapter.

    :param request_id:
        Identifier of the request consumed by the settlement transaction.
    :param receiver:
        Receiver whose denomination-token balance was observed.
    :param denomination_token:
        Token paid by the direct settlement.
    :param raw_balance_before:
        Receiver balance immediately before the settlement transaction.
    :param raw_balance_after:
        Receiver balance immediately after the settlement transaction.
    :param event_name:
        Name of the decoded request-specific settlement event.
    :param transaction_hash:
        Transaction that emitted ``event_name`` and paid the receiver.
    """

    request_id: int
    receiver: HexAddress
    denomination_token: HexAddress
    raw_balance_before: int
    raw_balance_after: int
    event_name: str
    transaction_hash: HexBytes

    def has_positive_balance_delta(self) -> bool:
        """Return whether the observed direct payment increased the balance.

        :return:
            ``True`` only when the receiver received a positive raw amount.
        """
        return self.raw_balance_after > self.raw_balance_before


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

    #: Denomination tokens synthetically injected on an Anvil fork to let the
    #: settlement round complete, in the denomination token's raw units.
    #:
    #: Zero (the default) means the settlement used only real on-fork state.
    #: A **non-zero** value means the driver wrote synthetic token balance into
    #: the vault (e.g. topped up a Lagoon Safe that was short of redemption
    #: liquidity) to make the ticket claimable. In that case a ``claimable``
    #: result proves only that the settlement *mechanism* works on a fork — it
    #: does NOT prove the vault can currently pay this redemption onchain.
    #: ``supports_anvil_settlement=True`` similarly means "the driver can
    #: advance tickets on a fork", not "the vault is solvent". Callers that need
    #: a real-liquidity guarantee must assert this is zero.
    synthetic_assets_injected_raw: int = 0

    #: ``True`` when the Anvil-only simulation explicitly bypassed a protocol
    #: liquidity admission check. This is separate from
    #: :attr:`synthetic_assets_injected_raw`: a protocol mock can relax a
    #: ``maxRedeem``-style gate without minting any denomination tokens.
    #:
    #: A result with this flag set proves the guarded call and settlement
    #: mechanics only. It is never evidence that the live deployment has
    #: enough immediately available redemption liquidity.
    liquidity_constraints_ignored: bool = False

    #: Evidence for terminal direct-payout protocols such as Ember.  It is
    #: required whenever ``status_after`` is ``none`` for an asynchronous
    #: settlement result.
    direct_payout_evidence: VaultDirectPayoutEvidence | None = None

    def is_terminal_success(self) -> bool:
        """Return whether this result proves its protocol settlement finished.

        Claimable tickets are terminal settlement success: the caller can now
        execute the ordinary protocol claim.  A protocol that pays directly
        may instead return ``none`` only with request-specific event evidence
        and a positive receiver balance delta.  A pending, missing, or
        evidence-free consumed status is never a successful forced settlement.

        :return:
            ``True`` for a synchronous no-op, a claimable async ticket, or a
            validated direct-payout terminal transition.
        """
        if not self.settlement_required:
            if self.ticket is None:
                return self.status_before is None and self.status_after is None and not self.transaction_hashes
            return self.status_after is AsyncVaultRequestStatus.claimable and not self.transaction_hashes and self.direct_payout_evidence is None

        if self.ticket is None or not self.transaction_hashes:
            return False

        if self.status_after is AsyncVaultRequestStatus.claimable:
            return self.direct_payout_evidence is None

        evidence = self.direct_payout_evidence
        if self.status_after is not AsyncVaultRequestStatus.none or evidence is None or not isinstance(self.ticket, RedemptionTicket):
            return False

        return evidence.request_id == self.ticket.get_request_id() and Web3.to_checksum_address(evidence.receiver) == Web3.to_checksum_address(self.ticket.to) and bool(evidence.event_name) and evidence.transaction_hash in self.transaction_hashes and evidence.has_positive_balance_delta()


def create_synchronous_settlement_result() -> VaultForcedSettlementResult:
    """Create the standard no-op outcome for a synchronous vault flow.

    Synchronous requests complete in their own transaction and therefore have
    no ticket or settlement status to advance. Managers exposing mixed flows
    reuse this value for their synchronous direction.

    :return:
        A zero-transaction result indicating no settlement is required.
    """
    return VaultForcedSettlementResult(
        ticket=None,
        settlement_required=False,
        status_before=None,
        status_after=None,
    )


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


@dataclass(frozen=True, slots=True)
class GuardV0ValidationCall:
    """One independently validated manager-generated call.

    :param target:
        Contract address selected by the deposit manager.
    :param calldata:
        Complete ABI-encoded call data, including its selector.
    :param selector:
        First four bytes of ``calldata`` retained for consumer evidence.
    """

    #: Contract selected by the vault manager.
    target: HexAddress

    #: Complete manager-generated call data.
    calldata: HexStr

    #: Function selector extracted from the call data.
    selector: HexBytes


@dataclass(frozen=True, slots=True)
class ClosedDepositGuardValidation:
    """Evidence returned after GuardV0 accepts a closed-vault deposit call.

    This proves GuardV0 compatibility of independently validated manager
    calls. It does not prove an ERC-20 approval sequence, a successful vault
    deposit or a minted share balance.

    :param vault_address:
        Vault whose closed deposit preflight produced the call.
    :param owner:
        Safe/SimpleVault address used as the deposit receiver.
    :param raw_amount:
        Requested denomination amount in raw units.
    :param closure_reason:
        Original typed closure prose retained for reporting.
    :param preflight_result:
        Typed closure result which authorised validation-only construction.
    :param calls:
        Manager-generated calls accepted by GuardV0.
    """

    #: Vault that remains closed to an actual deposit.
    vault_address: HexAddress

    #: Safe/SimpleVault used as deposit owner and receiver.
    owner: HexAddress

    #: Raw denomination amount requested for the validated deposit flow.
    raw_amount: int

    #: Human-readable closure reason from the manager preflight.
    closure_reason: str

    #: Stable manager closure result, e.g. ``deposit_closed``.
    preflight_result: str

    #: All calls which GuardV0 accepted independently.
    calls: tuple[GuardV0ValidationCall, ...]


def validate_closed_deposit_request_with_guard(
    request: DepositRequest,
    closure: VaultFlowUnavailable,
    guard: Contract,
    asset_manager: HexAddress,
) -> ClosedDepositGuardValidation:
    """Validate manager-generated closed-deposit calls through GuardV0.

    The request must come from
    :meth:`VaultDepositManager.create_deposit_request_for_guard_validation`
    after a typed closure preflight. Each manager-generated call is encoded and
    passed separately to the supplied GuardV0-compatible contract using a
    static call. The helper neither creates an ERC-20 approval nor broadcasts
    a transaction, so it cannot establish approval ordering or prove that the
    closed vault would accept a deposit.

    :param request:
        Validation-only deposit request generated by the selected manager.
    :param closure:
        Original typed deposit closure which authorised this diagnostic path.
    :param guard:
        Configured GuardV0-compatible contract exposing ``validateCall()``.
    :param asset_manager:
        Non-governance delegated asset-manager address to validate as.
    :return:
        Closure context plus every independently GuardV0-accepted target and
        selector, suitable for a consumer's persisted outcome evidence.
    :raise ValueError:
        If ``closure`` is not a matching typed closed or paused deposit result.
    """
    if closure.direction != "deposit" or closure.preflight_result not in {"deposit_closed", "deposit_paused"}:
        message = "Closed-deposit Guard validation requires a typed deposit_closed or deposit_paused preflight"
        raise ValueError(message)
    if closure.vault_address != request.vault.address or closure.caller != request.owner:
        message = "Closed-deposit Guard validation request must match the preflight vault and owner"
        raise ValueError(message)

    encoded_calls: list[GuardV0ValidationCall] = []
    for func in request.funcs:
        target, calldata = encode_simple_vault_transaction(func)
        guard.functions.validateCall(asset_manager, target, calldata).call()
        encoded_calls.append(
            GuardV0ValidationCall(
                target=HexAddress(target),
                calldata=calldata,
                selector=HexBytes(calldata)[:4],
            )
        )

    return ClosedDepositGuardValidation(
        vault_address=request.vault.address,
        owner=request.owner,
        raw_amount=request.raw_amount,
        closure_reason=closure.reason,
        preflight_result=closure.preflight_result,
        calls=tuple(encoded_calls),
    )


class VaultDepositManager(ABC):
    """Abstract base for every vault deposit and redemption flow.

    New public manager integrations must follow
    :file:`eth_defi/erc_4626/README-vault-protocol-support.md`: a manager is
    supported only after its complete lifecycle executes through ``GuardV0`` on
    an Anvil fork, with a protocol-shaped mock where fork state cannot cover a
    required path.

    A deposit manager wraps one :class:`~eth_defi.vault.base.VaultBase` and
    hides the differences between synchronous ERC-4626 vaults, asynchronous
    ERC-7540 vaults, and protocol-specific variants (Lagoon, Gains, IPOR,
    Ember, cSigma, and others) behind one request/settle/claim interface. This
    base class is policy-agnostic: it defines the contract and provides only
    the shared whitelist preflight and the Anvil no-op settlement; concrete
    subclasses supply the actual onchain behaviour.

    **Deposit process**

    A deposit is built with :meth:`create_deposit_request`, which returns a
    :class:`DepositRequest` wrapper of one or more transactions to sign and
    broadcast. The flow may be synchronous or asynchronous depending on the
    subclass — :meth:`has_synchronous_deposit` reports which. The owner must
    first ``approve()`` the ERC-20 denomination token to the spender returned by
    :meth:`get_deposit_approval_target` (the vault address by default). For a
    synchronous vault the request transaction mints shares immediately to the
    receiver. For an asynchronous vault the request transaction only registers a
    request; the shares are later claimed once settled via
    :meth:`can_finish_deposit` and :meth:`finish_deposit`. The receiver defaults
    to ``owner``; a separate ``to`` receiver is only honoured where the protocol
    supports it. Progress is tracked with a :class:`DepositTicket`.

    **Redemption process**

    A redemption is built with :meth:`create_redemption_request`, returning a
    :class:`RedemptionRequest` wrapper. :meth:`has_synchronous_redemption`
    reports whether the flow is synchronous (shares burned and assets returned
    in the request transaction) or asynchronous (request now, settle, then
    claim). The asynchronous lifecycle is: create the request, broadcast, parse
    the resulting :class:`RedemptionTicket`, wait for the redemption delay or
    operator settlement, then claim with :meth:`finish_redemption`. Some
    operator-finalised protocols pay the receiver directly and return ``None``
    from :meth:`finish_redemption`; :meth:`fetch_completed_redemption_tx_hash`
    locates that terminal transaction instead. A request is identified by its
    :class:`RedemptionTicket` (owner, receiver, raw shares, request transaction
    hash, and a protocol request id via ``RedemptionTicket.get_request_id()``).

    **Queues and settlement**

    Asynchronous protocols queue pending requests and settle them off the
    common interface (epoch rollovers, an operator/curator transaction, a
    settlement silo, etc.); :meth:`get_deposit_request_status` and
    :meth:`get_redemption_request_status` map protocol state onto
    :class:`AsyncVaultRequestStatus` (``none``/``pending``/``claimable``/
    ``reclaimable``), and :meth:`fetch_vault_flow_events` streams pending
    requests from an indexed backend. Synchronous subclasses have no queue: the
    request transaction is the settlement.

    **Lockups and cooldowns**

    Any lockup, cooldown, redemption delay or epoch window is protocol-specific.
    :meth:`estimate_redemption_delay` returns the vault-wide delay (not
    account-specific), and :meth:`get_redemption_delay_over` returns the naive
    UTC time an account may claim, or ``None`` when there is no deterministic
    onchain deadline. :meth:`can_create_redemption_request` reflects windows
    such as an epoch that only accepts requests on some days. Synchronous
    subclasses report a zero delay.

    **Whitelisting / access control**

    Deposit admission is enforced by :meth:`check_deposit_whitelist`, the shared
    preflight that every subclass must call before returning a request. It
    raises :class:`WhitelistingRequired` only when the vault's whitelist policy
    is applicable and queryable and the owner is provably not admitted; when the
    policy cannot be determined it stays silent and lets a genuine denial
    surface as an onchain revert. Subclasses needing a stricter fail-closed
    policy may additionally raise :class:`VaultFlowUnavailable`.

    **Anvil settlement (force_settle)**

    :meth:`force_settle` advances a pending ticket on an Anvil fork for
    integration tests. It requires an Anvil provider. For synchronous managers
    it is a no-op (called with ``None``, returns a not-settlement-required
    result) because the request transaction already completed the lifecycle.
    Asynchronous managers must override it with a protocol-specific driver; the
    base raises :class:`UnsupportedVaultSimulation` when no safe driver exists.
    ``ignore_liquidity=False`` preserves a real-liquidity simulation. The
    opt-in ``ignore_liquidity=True`` is valid only for an explicitly documented
    mock/fork driver and its result must mark
    :attr:`VaultForcedSettlementResult.liquidity_constraints_ignored`; it is
    never live redemption evidence or a way to bypass production preflights.
    """

    def __init__(
        self,
        vault: "eth_defi.vault.base.VaultBase",
    ):
        self.vault = vault

    @property
    def web3(self) -> Web3:
        return self.vault.web3

    def check_deposit_whitelist(self, owner: HexAddress) -> None:
        """Reject a deposit when the vault's whitelist excludes the owner.

        Shared deposit-preflight helper implementing the whitelisting contract
        every manager must honour: when a vault applies a deposit whitelist
        policy that is applicable and queryable, and ``owner`` is not a member
        of it, raise :class:`WhitelistingRequired` before any transaction is
        broadcast so the caller can surface a "whitelisting required" state
        instead of paying gas for a guaranteed revert.

        The check is intentionally conservative — it only raises when the
        whitelist information *can* be obtained and *is* applicable:

        - if :meth:`~eth_defi.vault.base.VaultBase.is_whitelisted_deposit`
          raises :class:`NotImplementedError`, the vault-wide policy cannot be
          determined for this adapter/version, so no exception is raised;
        - if the vault is permissionless, no exception is raised;
        - if :meth:`~eth_defi.vault.base.VaultBase.is_account_whitelisted`
          raises :class:`NotImplementedError`, per-account membership cannot be
          queried, so no exception is raised;
        - only when the policy is applicable and the owner is provably not
          admitted is :class:`WhitelistingRequired` raised.

        Adapters that need a stricter fail-closed policy for an unknown
        admission state should override their own preflight and raise
        :class:`VaultFlowUnavailable` in addition to calling this helper (see
        the Lagoon manager for an example).

        :param owner:
            Deposit owner and controller whose whitelist membership is checked.
        :raise WhitelistingRequired:
            When the vault applies an applicable, queryable whitelist policy
            and ``owner`` is not permitted to deposit.
        """
        try:
            whitelist_applies = self.vault.is_whitelisted_deposit()
        except NotImplementedError:
            # Whitelist policy cannot be determined for this adapter/version;
            # a genuine denial will surface as an onchain revert instead.
            return

        if not whitelist_applies:
            # Permissionless vault.
            return

        try:
            account_allowed = self.vault.is_account_whitelisted(owner)
        except NotImplementedError:
            # Vault-wide policy is known but per-account membership is not
            # queryable, so the individual owner cannot be preflighted.
            return

        if account_allowed:
            return

        # The message must be self-describing for diagnostics: embed the chain
        # id, vault address and depositor address directly in the text, not
        # only in the structured fields (see WhitelistingRequired docstring).
        raise WhitelistingRequired(
            f"Depositor {owner} is not whitelisted for vault {self.vault.address} on chain {self.vault.chain_id}",
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            caller=owner,
            direction="deposit",
            phase="preflight",
        )

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Force the selected ticket forward on an Anvil simulation.

        Synchronous managers do not require settlement and return a no-op
        result when called with None. Asynchronous managers must override this
        method and supply their request ticket.

        :param ticket:
            Pending async request ticket, or None for a synchronous flow.
        :param mock:
            Optional deployed protocol mock used only by focused local tests.
            Concrete asynchronous managers may use it to execute their
            operator/keeper settlement path without broadening production
            Anvil-fork authority. Passing a mock to a manager that does not
            implement mock settlement remains a typed unsupported simulation.
        :param ignore_liquidity:
            Permit a protocol-specific, Anvil-only mock or fork driver to
            bypass an otherwise unavailable redemption-liquidity gate. Defaults
            to ``False``. Managers must reject this request unless they have a
            tested, explicit implementation; it must never weaken a production
            preflight or live settlement path.
        :return:
            Settlement outcome with before/after status and transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil or an async manager lacks a driver.
        """
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                f"{self.__class__.__name__}.force_settle() requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )

        if ignore_liquidity:
            raise UnsupportedVaultSimulation(
                f"{self.__class__.__name__} has no Anvil liquidity-bypass simulation driver",
                unsupported_reason="liquidity_bypass_simulation_not_implemented",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="deposit" if isinstance(ticket, DepositTicket) else "redeem" if isinstance(ticket, RedemptionTicket) else None,
            )

        if mock is not None:
            message = f"{self.__class__.__name__} has no local mock settlement driver"
            raise UnsupportedVaultSimulation(
                message,
                unsupported_reason="mock_settlement_driver_not_implemented",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
            )

        if ticket is None and (self.has_synchronous_deposit() or self.has_synchronous_redemption()):
            return create_synchronous_settlement_result()

        raise UnsupportedVaultSimulation(
            f"{self.__class__.__name__} has no Anvil settlement driver for {type(ticket).__name__}",
            unsupported_reason="anvil_settlement_driver_not_implemented",
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            direction="deposit" if isinstance(ticket, DepositTicket) else "redeem" if isinstance(ticket, RedemptionTicket) else None,
        )

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
        """Build the deposit request transaction(s) for an owner.

        Abstracts the ERC-4626, ERC-7540, Lagoon and other protocol deposit
        flows behind a common request wrapper.

        Whitelisting contract: implementations **must** raise
        :class:`WhitelistingRequired` before returning a request when the
        vault applies a deposit whitelist policy, that policy is applicable
        and queryable, and ``owner`` is not permitted to deposit. Call
        :meth:`check_deposit_whitelist` at the start of the preflight to
        satisfy this contract. When the whitelist information cannot be
        obtained (the adapter's whitelist reads raise
        :class:`NotImplementedError`) the manager must not raise
        :class:`WhitelistingRequired`; it either proceeds — letting any real
        denial surface as an onchain revert — or fails closed with a plain
        :class:`VaultFlowUnavailable` when unknown admission is unsafe.

        :param owner:
            Deposit owner and controller.
        :param to:
            Optional separate receiver, where the protocol supports it.
        :param amount:
            Human-readable denomination-token amount, converted using the
            denomination token decimals when ``raw_amount`` is not given.
        :param raw_amount:
            Raw denomination-token amount, overriding ``amount``.
        :param check_max_deposit:
            Preflight the deposit against the vault's deposit capacity.
        :param check_enough_token:
            Preflight that ``owner`` holds enough denomination token.
        :return:
            Deposit request wrapper ready for signing and parsing.
        :raise WhitelistingRequired:
            If the vault whitelist is applicable and excludes ``owner``.
        :raise VaultFlowUnavailable:
            If the deposit cannot be safely prepared before broadcast.
        """

    def create_deposit_request_for_guard_validation(
        self,
        owner: HexAddress,
        raw_amount: int,
    ) -> DepositRequest:
        """Build deposit calldata for a closed-vault GuardV0 policy check.

        This Anvil-only diagnostic path is for a consumer that has already
        received a typed ``deposit_closed`` or ``deposit_paused`` preflight
        result. Adapters must override it only after proving that their typed
        result represents a temporary vault closure rather than a capacity or
        amount restriction. The returned calls must be supplied individually to
        ``GuardV0.validateCall()``; callers must never broadcast them to the
        closed protocol vault.

        :param owner:
            SimpleVaultV0/Safe address that would own the shares.
        :param raw_amount:
            Denomination-token amount in the selected asset's raw unit.
        :return:
            Manager-generated deposit request suitable only for isolated
            GuardV0 validation.
        :raise UnsupportedVaultSimulation:
            Unless the protocol-specific manager implements this diagnostic
            path.
        """
        self._assert_anvil_guard_validation()
        reason = f"{self.__class__.__name__} has no proven closed-deposit Guard validation path for owner {owner} and raw amount {raw_amount}"
        raise UnsupportedVaultSimulation(
            reason,
            unsupported_reason="closed_deposit_guard_validation_not_implemented",
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            direction="deposit",
            phase="guard_validation",
        )

    def _assert_anvil_guard_validation(self) -> None:
        """Reject closed-deposit Guard validation outside an Anvil fork.

        The validation-only constructor deliberately omits temporary live
        availability checks. Restricting it to an Anvil provider prevents a
        production caller from accidentally constructing a bypassed request
        and makes the diagnostic boundary machine-readable.

        :raise UnsupportedVaultSimulation:
            If this manager is not connected to Anvil.
        """
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                f"{self.__class__.__name__}.create_deposit_request_for_guard_validation() requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="deposit",
                phase="guard_validation",
            )

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
            onchain deadline.

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

        - Default returns ``None``: the protocol has no deterministic onchain
          settlement schedule (e.g. operator-driven ERC-7540 vaults like Lagoon).
          Subclasses with a predictable settlement cadence (e.g. Ostium V1.5)
          override this to return an estimated UTC timestamp.

        :param address:
            Owner of the pending deposit request.

        :return:
            Naive UTC timestamp when the deposit is expected to settle, or
            ``None`` when no onchain estimate is available.
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
