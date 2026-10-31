"""Atoma synchronous deposits and epoch-based withdrawal claims.

The verified contracts are linked in ``eth_defi/abi/atoma/README.md``.
Requests escrow shares, operator settlement burns them, and the owner's later
claim pays net USDC. A settled liability is neither liquid cash nor an active
share balance. Keep persisted tickets until the claim receipt is accounted for.
"""

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError
from web3.types import TxReceipt

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked
from eth_defi.provider.anvil import is_anvil, make_anvil_custom_rpc_request, mine
from eth_defi.revert_reason import extract_revert_data
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
)
from eth_defi.vault.flow_events import (
    PendingVaultFlow,
    VaultFlowDirection,
    create_pending_vault_flow,
    fetch_vault_flow_logs_hypersync,
    run_vault_flow_log_fetch,
)

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.atoma.vault import AtomaVault

logger = logging.getLogger(__name__)

#: Only this known custom error is a retryable claim liquidity condition.
INSUFFICIENT_IDLE = bytes(Web3.keccak(text="InsufficientIdle()")[:4])


@dataclass(frozen=True, slots=True)
class AtomaRequestPart:
    """Canonical receipt evidence contributing to one owner/epoch claim."""

    #: Successful request transaction.
    tx_hash: HexBytes
    #: Canonical request block.
    block_number: int
    #: Block identity for reorganisation detection.
    block_hash: HexBytes
    #: Event identity within the transaction's block.
    log_index: int
    #: Shares contributed by this event, not the aggregate claim.
    raw_shares: int

    @property
    def identity(self) -> str:
        """Return the stable transaction/log key used for supersession.

        :return: Hex transaction hash followed by the log index.
        """
        return f"{self.tx_hash.to_0x_hex()}:{self.log_index}"


@dataclass(slots=True)
class AtomaRedemptionTicket(RedemptionTicket):
    """Persist an owner/epoch claim and all its contributing requests."""

    #: Custody chain, never inferred from an arbitrary current provider.
    chain_id: int
    #: Epoch in which the request actually executed.
    request_epoch: int
    #: Epoch selected by the mined event, also the protocol request id.
    settlement_epoch: int
    #: Implementation observed at the first request receipt.
    implementation: HexAddress
    #: Implementation checked before submission.
    preflight_implementation: HexAddress
    #: Every canonical request represented by this aggregate ticket.
    parts: tuple[AtomaRequestPart, ...]
    #: Prevent normal claiming until the consumer has persisted supersession.
    reconciliation_required: bool = False

    def get_request_id(self) -> int:
        return self.settlement_epoch


@dataclass(slots=True)
class AtomaFlowAnalysis(DepositRedeemEventAnalysis):
    """Actual Atoma flow, including a possible zero-value loss realisation."""

    #: Gross claim before the exit fee, or the deposit amount.
    gross_assets: Decimal
    #: Exit fee already excluded from denomination_amount.
    withdrawal_fee: Decimal
    #: Verified implementation used to interpret the mined transaction.
    implementation: HexAddress
    #: Request contributions; consumers must not credit these independently.
    request_parts: tuple[AtomaRequestPart, ...] = ()

    def __post_init__(self) -> None:
        assert self.denomination_amount >= 0
        assert self.share_count > 0
        assert self.withdrawal_fee >= 0
        assert self.gross_assets == self.denomination_amount + self.withdrawal_fee

    def is_success(self) -> bool:
        return True


@dataclass(slots=True)
class AtomaRedemptionRequest(RedemptionRequest):
    """Capture the mined request rather than predicting its settlement epoch."""

    #: Version checked before submission, retained for upgrade reconciliation.
    implementation: HexAddress

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> AtomaRedemptionTicket:
        manager = self.vault.get_deposit_manager()
        receipt = self.web3.eth.get_transaction_receipt(tx_hashes[-1])
        if receipt["status"] != 1:
            raise CannotParseRedemptionTransaction("Atoma withdrawal request reverted")
        events = manager._receipt_events(self.vault.vault_contract.events.WithdrawalRequested, receipt)
        if len(events) != 1 or events[0]["args"]["user"].lower() != self.owner.lower() or int(events[0]["args"]["shares"]) != self.raw_shares:
            raise CannotParseRedemptionTransaction("Expected one matching Atoma withdrawal request")
        ticket = manager._ticket_from_event(receipt, events[0], self.implementation)
        # Inclusion can cross an epoch boundary or aggregate with another tx.
        # Keep the mined evidence even when ordinary claiming must be deferred.
        if not ticket.reconciliation_required:
            aggregate = self.vault.vault_contract.functions.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=receipt["blockNumber"])
            ticket.reconciliation_required = aggregate != ticket.raw_shares
        return ticket


class AtomaDepositManager(ERC4626DepositManager):
    """Execute deposits and persist request/settlement/claim withdrawals.

    Deposit and claim receivers are the custody owner. Claims consume an
    entire owner/epoch aggregate, so independently created same-epoch requests
    must be reconciled and atomically superseded by the consumer before claim.
    Operator settlement is exposed only through the Anvil simulation hook.
    """

    vault: "AtomaVault"

    def _unavailable(self, reason: str, owner: HexAddress | None = None, phase: str = "preflight") -> VaultFlowUnavailable:
        """Build a contextual error without treating RPC failures as availability.

        :param reason: Stable reason for deferred or unsupported execution.
        :param owner: Custody account when known.
        :param phase: Lifecycle phase.
        :return: Structured flow exception.
        """
        return VaultFlowUnavailable(reason, protocol="Atoma", vault_address=self.vault.address, caller=owner, phase=phase, preflight_result=reason)

    def _amount(self, decimal: Decimal | None, raw: int | None, *, shares: bool) -> int:
        """Validate mutually exclusive input units before constructing calldata.

        :param decimal: Human-readable input.
        :param raw: Integer token units.
        :param shares: Whether to use the share token rather than USDC.
        :return: Strictly positive raw units.
        """
        if (decimal is None) == (raw is None):
            raise ValueError("Supply exactly one decimal or raw amount")
        token = self.vault.share_token if shares else self.vault.denomination_token
        if raw is None:
            raw = token.convert_to_raw(decimal)
        if type(raw) is not int or raw <= 0:
            raise ValueError("Atoma amount must be a positive integer in raw units")
        return raw

    def _owner(self, owner: HexAddress, to: HexAddress | None) -> HexAddress:
        """Enforce the custody caller/receiver boundary.

        :param owner: Actual caller of the Atoma contract.
        :param to: Requested recipient, defaulting to owner.
        :return: Checksum custody address.
        """
        owner = Web3.to_checksum_address(owner)
        if int(owner, 16) == 0 or (to is not None and Web3.to_checksum_address(to) != owner):
            raise ValueError("Atoma receiver must equal the non-zero custody owner")
        return owner

    def create_deposit_request(self, owner: HexAddress, to: HexAddress | None = None, amount: Decimal | None = None, raw_amount: int | None = None, check_max_deposit: bool = True, check_enough_token: bool = True) -> ERC4626DepositRequest:
        owner = self._owner(owner, to)
        raw = self._amount(amount, raw_amount, shares=False)
        block = self.web3.eth.block_number
        self.vault.check_execution_version(block)
        self.check_deposit_whitelist(owner)
        contract = self.vault.vault_contract
        minimum = contract.functions.MIN_DEPOSIT().call(block_identifier=block)
        if raw < minimum:
            raise self._unavailable("below_minimum", owner)
        # A caller cannot bypass protocol admission with generic check flags.
        if raw > contract.functions.maxDeposit(owner).call(block_identifier=block):
            raise self._unavailable("deposit_capacity", owner)
        if check_enough_token and raw > self.vault.denomination_token.fetch_raw_balance_of(owner, block_identifier=block):
            raise self._unavailable("insufficient_assets", owner)
        return ERC4626DepositRequest(vault=self.vault, owner=owner, to=owner, amount=self.vault.denomination_token.convert_to_decimals(raw), raw_amount=raw, funcs=[contract.functions.deposit(raw, owner)])

    def create_deposit_request_for_guard_validation(self, owner: HexAddress, raw_amount: int) -> ERC4626DepositRequest:
        # The inherited bypass would omit Atoma's version/minimum checks.
        raise UnsupportedVaultSimulation("Atoma closed-deposit validation is not certified", unsupported_reason="atoma_closed_deposit_validation_unsupported")

    def fetch_unlocked_shares(self, owner: HexAddress, block_identifier: BlockIdentifier = "latest") -> int:
        """Read shares available after the current deposit lock.

        :param owner: Custody account.
        :param block_identifier: State block, pinned when latest is requested.
        :return: Raw unlocked share balance.
        """
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        balance = f.balanceOf(owner).call(block_identifier=block)
        current = f.getCurrentEpoch().call(block_identifier=block)
        locked = f.lockedShares(owner).call(block_identifier=block) if f.depositEpoch(owner).call(block_identifier=block) >= current else 0
        return max(0, balance - locked)

    def create_redemption_request(self, owner: HexAddress, to: HexAddress | None = None, shares: Decimal | None = None, raw_shares: int | None = None, check_max_deposit: bool = True, check_enough_token: bool = True, check_max_redeem: bool = True) -> AtomaRedemptionRequest:
        owner = self._owner(owner, to)
        raw = self._amount(shares, raw_shares, shares=True)
        block = self.web3.eth.block_number
        implementation = self.vault.check_execution_version(block)
        if check_enough_token and raw > self.fetch_unlocked_shares(owner, block):
            raise self._unavailable("insufficient_unlocked_shares", owner)
        f = self.vault.vault_contract.functions
        epoch = f.getCurrentEpoch().call(block_identifier=block) + 1
        # The executor reconstructs a mined request with check_enough_token=False.
        # Its shares are already escrowed; admission applies only to new requests.
        if check_enough_token and f.userEpochShares(epoch, owner).call(block_identifier=block):
            raise self._unavailable("epoch_request_already_exists", owner)
        return AtomaRedemptionRequest(vault=self.vault, owner=owner, to=owner, shares=self.vault.share_token.convert_to_decimals(raw), raw_shares=raw, funcs=[f.requestWithdrawal(raw)], implementation=implementation)

    @staticmethod
    def _receipt_events(event: Any, receipt: TxReceipt) -> list[Any]:
        """Filter decoded receipt events to their bound contract address.

        :param event: Bound contract event.
        :param receipt: Mined transaction receipt.
        :return: Events emitted by the intended contract only.
        """
        return [log for log in event().process_receipt(receipt, errors=EventLogErrorFlags.Discard) if log["address"].lower() == event.address.lower()]

    def _ticket_from_event(self, receipt: TxReceipt, event: Any, preflight: HexAddress | None = None) -> AtomaRedemptionTicket:
        """Retain successful request evidence even if it needs reconciliation.

        :param receipt: Request receipt.
        :param event: Matching WithdrawalRequested event.
        :param preflight: Expected implementation, if submitted by this manager.
        :return: Persistable mined request.
        """
        args = event["args"]
        if int(args["settlementEpoch"]) != int(args["requestEpoch"]) + 1 or int(args["shares"]) <= 0:
            raise CannotParseRedemptionTransaction("Invalid Atoma request epoch or shares")
        block = int(receipt["blockNumber"])
        if HexBytes(self.web3.eth.get_block(block)["hash"]) != HexBytes(receipt["blockHash"]):
            raise CannotParseRedemptionTransaction("Atoma request receipt is not canonical")
        implementation = self.vault.fetch_implementation(block)
        reconcile = False
        try:
            self.vault.fetch_receipt_contract(block)
        except ValueError:
            reconcile = True
        reconcile |= preflight is not None and preflight != implementation
        owner = Web3.to_checksum_address(args["user"])
        part = AtomaRequestPart(HexBytes(receipt["transactionHash"]), block, HexBytes(receipt["blockHash"]), int(event["logIndex"]), int(args["shares"]))
        return AtomaRedemptionTicket(vault_address=self.vault.address, owner=owner, to=owner, raw_shares=part.raw_shares, tx_hash=part.tx_hash, chain_id=self.vault.chain_id, request_epoch=int(args["requestEpoch"]), settlement_epoch=int(args["settlementEpoch"]), implementation=implementation, preflight_implementation=preflight or implementation, parts=(part,), reconciliation_required=reconcile)

    def _validate_ticket(self, ticket: AtomaRedemptionTicket, *, canonical: bool = False) -> None:
        """Validate persisted identity and optionally its canonical block evidence.

        :param ticket: Persisted claim.
        :param canonical: Check request blocks against the connected chain.
        :return: None; invalid tickets raise ValueError.
        """
        if not isinstance(ticket, AtomaRedemptionTicket) or ticket.chain_id != self.vault.spec.chain_id or ticket.vault_address.lower() != self.vault.address.lower():
            raise ValueError("Atoma ticket chain or vault mismatch")
        self._owner(ticket.owner, ticket.to)
        if ticket.settlement_epoch != ticket.request_epoch + 1 or ticket.request_epoch < 0 or not ticket.parts or ticket.raw_shares <= 0:
            raise ValueError("Invalid Atoma ticket epoch or amount")
        if len({p.identity for p in ticket.parts}) != len(ticket.parts) or sum(p.raw_shares for p in ticket.parts) != ticket.raw_shares:
            raise ValueError("Invalid Atoma request contributions")
        if ticket.tx_hash != ticket.parts[0].tx_hash:
            raise ValueError("Atoma primary transaction does not match contributions")
        for part in ticket.parts:
            if part.raw_shares <= 0 or part.block_number < 0 or part.log_index < 0 or len(part.tx_hash) != 32 or len(part.block_hash) != 32:
                raise ValueError("Malformed Atoma request evidence")
            if canonical:
                if HexBytes(self.web3.eth.get_block(part.block_number)["hash"]) != part.block_hash:
                    raise self._unavailable("request_reorganisation", ticket.owner, "claim")
                receipt = self.web3.eth.get_transaction_receipt(part.tx_hash)
                events = self._receipt_events(self.vault.vault_contract.events.WithdrawalRequested, receipt)
                matching = [e for e in events if e["logIndex"] == part.log_index and e["args"]["user"].lower() == ticket.owner.lower() and e["args"]["shares"] == part.raw_shares and e["args"]["requestEpoch"] == ticket.request_epoch and e["args"]["settlementEpoch"] == ticket.settlement_epoch]
                if receipt["status"] != 1 or HexBytes(receipt["blockHash"]) != part.block_hash or receipt["blockNumber"] != part.block_number or len(matching) != 1:
                    raise self._unavailable("request_evidence_mismatch", ticket.owner, "claim")

    def serialize_redemption_ticket(self, ticket: AtomaRedemptionTicket) -> dict:
        self._validate_ticket(ticket)
        return {**super().serialize_redemption_ticket(ticket), "atoma_schema": 1, "atoma_chain_id": ticket.chain_id, "atoma_request_epoch": str(ticket.request_epoch), "atoma_settlement_epoch": str(ticket.settlement_epoch), "atoma_implementation": ticket.implementation, "atoma_preflight_implementation": ticket.preflight_implementation, "atoma_reconciliation_required": ticket.reconciliation_required, "atoma_parts": [{"tx_hash": p.tx_hash.to_0x_hex(), "block_number": p.block_number, "block_hash": p.block_hash.to_0x_hex(), "log_index": p.log_index, "raw_shares": str(p.raw_shares)} for p in ticket.parts]}

    def reconstruct_redemption_ticket(self, data: dict) -> AtomaRedemptionTicket:
        if data["atoma_schema"] != 1 or type(data["atoma_reconciliation_required"]) is not bool:
            raise ValueError("Unsupported Atoma ticket schema")
        ticket = AtomaRedemptionTicket(vault_address=Web3.to_checksum_address(data["vault_address"]), owner=Web3.to_checksum_address(data["vault_owner"]), to=Web3.to_checksum_address(data["vault_to"]), raw_shares=int(data["vault_raw_amount"]), tx_hash=HexBytes(data["vault_request_tx_hash"]), chain_id=int(data["atoma_chain_id"]), request_epoch=int(data["atoma_request_epoch"]), settlement_epoch=int(data["atoma_settlement_epoch"]), implementation=HexAddress(data["atoma_implementation"]), preflight_implementation=HexAddress(data["atoma_preflight_implementation"]), reconciliation_required=data["atoma_reconciliation_required"], parts=tuple(AtomaRequestPart(HexBytes(p["tx_hash"]), int(p["block_number"]), HexBytes(p["block_hash"]), int(p["log_index"]), int(p["raw_shares"])) for p in data["atoma_parts"]))
        self._validate_ticket(ticket)
        return ticket

    def get_redemption_request_status(self, ticket: AtomaRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> AsyncVaultRequestStatus:
        self._validate_ticket(ticket, canonical=True)
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        if not f.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=block):
            return AsyncVaultRequestStatus.none
        return AsyncVaultRequestStatus.claimable if f.getEpoch(ticket.settlement_epoch).call(block_identifier=block)[2] else AsyncVaultRequestStatus.pending

    def _claim_call(self, ticket: AtomaRedemptionTicket) -> ContractFunction:
        """Validate custody, aggregate and implementation before building a claim.

        :param ticket: Persisted and, if needed, reconciled request.
        :return: Exact all-or-nothing claim call.
        """
        self._validate_ticket(ticket, canonical=True)
        block = self.web3.eth.block_number
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        if ticket.reconciliation_required or f.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=block) != ticket.raw_shares:
            raise self._unavailable("request_reconciliation_required", ticket.owner, "claim")
        if not f.getEpoch(ticket.settlement_epoch).call(block_identifier=block)[2]:
            raise self._unavailable("epoch_not_settled", ticket.owner, "claim")
        return f.claimWithdrawal(ticket.settlement_epoch)

    def can_finish_redeem(self, redemption_ticket: AtomaRedemptionTicket) -> bool:
        if self.get_redemption_request_status(redemption_ticket) is not AsyncVaultRequestStatus.claimable:
            return False
        call = self._claim_call(redemption_ticket)
        try:
            call.call({"from": redemption_ticket.owner})
        except (ContractLogicError, ValueError) as exc:
            if extract_revert_data(exc) == INSUFFICIENT_IDLE:
                return False
            raise
        return True

    def finish_redemption(self, redemption_ticket: AtomaRedemptionTicket) -> ContractFunction:
        if not self.can_finish_redeem(redemption_ticket):
            raise self._unavailable("claim_not_executable", redemption_ticket.owner, "claim")
        return self._claim_call(redemption_ticket)

    def has_synchronous_redemption(self) -> bool:
        return False

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        block = self.web3.eth.block_number
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        return f.maxDeposit(owner).call(block_identifier=block) >= f.MIN_DEPOSIT().call(block_identifier=block)

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        block = self.web3.eth.block_number
        if self.fetch_unlocked_shares(owner, block) == 0:
            return False
        f = self.vault.vault_contract.functions
        return f.userEpochShares(f.getCurrentEpoch().call(block_identifier=block) + 1, owner).call(block_identifier=block) == 0

    def fetch_pending_redemption_epochs(self, owner: HexAddress, block_identifier: BlockIdentifier = "latest") -> Iterator[int]:
        """Scan all epoch keys with bounded Multicall batches for owner diagnostics.

        Routine settlement polling should read a ticket instead. This diagnostic
        is O(epoch count), including old claims, and logs each batch's progress.

        :param owner: Custody owner.
        :param block_identifier: State snapshot.
        :return: Epoch ids with unclaimed shares.
        """
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        end = int(f.getCurrentEpoch().call(block_identifier=block)) + 2
        calls = [EncodedCall.from_contract_call(f.userEpochShares(epoch, owner), extra_data={"epoch": epoch}) for epoch in range(end)]
        logger.info("Reading %s Atoma epoch keys for %s at block %s", end, owner, block)
        for result in read_multicall_chunked(chain_id=self.vault.chain_id, web3factory=lambda: self.web3, calls=calls, block_identifier=block, max_workers=1, chunk_size=128, timestamped_results=False, backend="threading", progress_bar_desc="Atoma pending epochs"):
            if not result.success or len(result.result) != 32:
                raise self._unavailable("epoch_scan_incomplete", owner)
            if int.from_bytes(result.result, "big"):
                yield int(result.call.extra_data["epoch"])

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        return next(self.fetch_pending_redemption_epochs(owner), None) is not None

    def fetch_ticket_epoch_end(self, ticket: AtomaRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> datetime.datetime:
        """Read the earliest operator-settlement time, not a payout promise.

        :param ticket: Owner/epoch ticket.
        :param block_identifier: State snapshot.
        :return: Naive UTC epoch end.
        """
        self._validate_ticket(ticket)
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        return self.vault.fetch_epoch_end(ticket.settlement_epoch, block)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        block = self.web3.eth.block_number
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        epoch = f.getCurrentEpoch().call(block_identifier=block)
        if f.depositEpoch(address).call(block_identifier=block) >= epoch and f.lockedShares(address).call(block_identifier=block):
            return datetime.datetime.fromtimestamp(f.getEpochEndTime(epoch).call(block_identifier=block), datetime.UTC).replace(tzinfo=None)
        return None

    def estimate_redemption_delay(self) -> datetime.timedelta:
        block = self.web3.eth.block_number
        f = self.vault.vault_contract.functions
        epoch = f.getCurrentEpoch().call(block_identifier=block)
        return datetime.timedelta(seconds=f.getEpochEndTime(epoch + 1).call(block_identifier=block) - self.web3.eth.get_block(block)["timestamp"])

    def estimate_redeem(self, owner: HexAddress | None, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        # Pre-request estimate includes the current settlement performance fee;
        # future NAV, haircuts and liquidity remain unknown.
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        raw = self.vault.share_token.convert_to_raw(shares)
        precision = f.NAV_PRECISION().call(block_identifier=block)
        supply = f.totalSupply().call(block_identifier=block)
        if supply == 0:
            return Decimal(0)
        nav = f.totalAssets().call(block_identifier=block) * precision // supply
        hwm = f.highWaterMark().call(block_identifier=block)
        nav -= max(0, nav - hwm) * f.PERFORMANCE_FEE_BPS().call(block_identifier=block) // 10_000
        gross = raw * nav // precision
        fee = gross * f.WITHDRAWAL_FEE_BPS().call(block_identifier=block) // 10_000
        return self.vault.denomination_token.convert_to_decimals(gross - fee)

    def fetch_claim_amounts(self, ticket: AtomaRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> tuple[int, int, int]:
        """Quote the current gross, exit fee and net claim with Solidity rounding.

        A settled claim can be haircut later; only a mined receipt is realised.

        :param ticket: Owner/epoch claim.
        :param block_identifier: State snapshot.
        :return: Raw USDC gross, fee and net payout.
        """
        self._validate_ticket(ticket, canonical=True)
        block = self.web3.eth.block_number if block_identifier == "latest" else block_identifier
        self.vault.check_execution_version(block)
        f = self.vault.vault_contract.functions
        _, nav, settled = f.getEpoch(ticket.settlement_epoch).call(block_identifier=block)
        if not settled:
            raise self._unavailable("epoch_not_settled", ticket.owner, "claim")
        shares = f.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=block)
        if ticket.reconciliation_required or shares != ticket.raw_shares:
            reason = "claim_already_completed" if shares == 0 else "request_reconciliation_required"
            raise self._unavailable(reason, ticket.owner, "valuation")
        gross = shares * nav // f.NAV_PRECISION().call(block_identifier=block)
        before = f.epochIndexAtSettle(ticket.settlement_epoch).call(block_identifier=block)
        now = f.shortfallIndexWad().call(block_identifier=block)
        if now < before:
            gross = gross * now // before
        fee = gross * f.WITHDRAWAL_FEE_BPS().call(block_identifier=block) // 10_000
        return gross, fee, gross - fee

    def _failure(self, tx_hash: HexBytes, reason: str, direction: str, status: int) -> DepositRedeemEventFailure:
        """Return an accounting failure while retaining the mined transaction.

        :param tx_hash: Receipt transaction.
        :param reason: Evidence problem.
        :param direction: Deposit or redeem.
        :param status: Mined execution status.
        :return: Shared structured failure.
        """
        return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=reason, protocol="Atoma", vault_address=self.vault.address, direction=direction, phase="claim" if direction == "redeem" else "deposit", receipt_status=status)

    def analyse_redemption(self, claim_tx_hash: HexBytes | str, redemption_ticket: AtomaRedemptionTicket) -> AtomaFlowAnalysis | DepositRedeemEventFailure:
        self._validate_ticket(redemption_ticket, canonical=True)
        return self._analyse(HexBytes(claim_tx_hash), redemption_ticket, None)

    def analyse_deposit(self, claim_tx_hash: HexBytes | str, deposit_ticket: DepositTicket | None) -> AtomaFlowAnalysis | DepositRedeemEventFailure:
        return self._analyse(HexBytes(claim_tx_hash), None, deposit_ticket)

    def _analyse(self, tx_hash: HexBytes, ticket: AtomaRedemptionTicket | None, deposit_ticket: DepositTicket | None) -> AtomaFlowAnalysis | DepositRedeemEventFailure:
        """Interpret realised token flows using the mined implementation's ABI.

        :param tx_hash: Successful or reverted flow transaction.
        :param ticket: Redemption evidence, absent for a deposit.
        :param deposit_ticket: Optional expected synchronous deposit.
        :return: Actual net assets or a retained accounting failure.
        """
        direction = "redeem" if ticket is not None else "deposit"
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            return self._failure(tx_hash, "Atoma transaction reverted", direction, int(receipt["status"]))
        try:
            if HexBytes(self.web3.eth.get_block(receipt["blockNumber"])["hash"]) != HexBytes(receipt["blockHash"]):
                raise ValueError("Receipt is no longer canonical")
            contract, implementation = self.vault.fetch_receipt_contract(int(receipt["blockNumber"]))
            event = contract.events.WithdrawalClaimed if ticket else contract.events.Deposit
            logs = self._receipt_events(event, receipt)
            if len(logs) != 1:
                raise ValueError("Expected exactly one matching Atoma flow event")
            a = logs[0]["args"]
            owner = Web3.to_checksum_address(a["user"] if ticket else a["owner"])
            shares, assets = int(a["shares"]), int(a["assets"])
            fee = int(a["fee"]) if ticket else 0
            if ticket and (ticket.reconciliation_required or owner.lower() != ticket.owner.lower() or int(a["epochId"]) != ticket.settlement_epoch or shares != ticket.raw_shares):
                raise ValueError("Atoma claim does not match the accounted ticket")
            if not ticket and (a["sender"].lower() != owner.lower() or (deposit_ticket and (deposit_ticket.vault_address.lower() != self.vault.address.lower() or owner.lower() != deposit_ticket.owner.lower() or assets != deposit_ticket.raw_amount))):
                raise ValueError("Atoma deposit does not match custody or expected assets")
            transfers = self._receipt_events(self.vault.denomination_token.contract.events.Transfer, receipt)
            sender, receiver = (self.vault.address, owner) if ticket else (owner, self.vault.address)
            if not any(t["args"]["from"].lower() == sender.lower() and t["args"]["to"].lower() == receiver.lower() and int(t["args"]["value"]) == assets for t in transfers):
                raise ValueError("Missing exact denomination-token transfer evidence")
            if shares <= 0 or assets < 0 or fee < 0:
                raise ValueError("Invalid Atoma event amounts")
        except ValueError as exc:
            return self._failure(tx_hash, str(exc), direction, 1)
        token = self.vault.denomination_token
        return AtomaFlowAnalysis(from_=owner, to=owner, denomination_amount=token.convert_to_decimals(assets), share_count=self.vault.share_token.convert_to_decimals(shares), tx_hash=tx_hash, block_number=receipt["blockNumber"], block_timestamp=get_block_timestamp(self.web3, receipt["blockNumber"]), gross_assets=token.convert_to_decimals(assets + fee), withdrawal_fee=token.convert_to_decimals(fee), implementation=implementation, request_parts=ticket.parts if ticket else ())

    def force_settle(self, ticket: DepositTicket | RedemptionTicket | None, *, mock: object | None = None, ignore_liquidity: bool = False) -> VaultForcedSettlementResult:
        if not is_anvil(self.web3) or mock is not None or ignore_liquidity:
            raise UnsupportedVaultSimulation("Atoma settlement requires unmodified Anvil state", unsupported_reason="atoma_anvil_state_required")
        if ticket is None:
            return create_synchronous_settlement_result()
        self._validate_ticket(ticket, canonical=True)
        before = self.get_redemption_request_status(ticket)
        if before is AsyncVaultRequestStatus.claimable:
            return VaultForcedSettlementResult(ticket, False, before, before)
        if before is not AsyncVaultRequestStatus.pending:
            raise UnsupportedVaultSimulation("Atoma request is not pending", unsupported_reason="atoma_request_not_pending")
        f = self.vault.vault_contract.functions
        end = int(f.getEpochEndTime(ticket.settlement_epoch).call())
        if self.web3.eth.get_block("latest")["timestamp"] < end:
            logger.info("Advancing Anvil to Atoma settlement epoch end %s", end)
            mine(self.web3, timestamp=end)
        operator = f.operator().call()
        logger.info("Impersonating Atoma operator %s and funding native gas; settling all requests in epoch %s", operator, ticket.settlement_epoch)
        make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [operator])
        try:
            make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [operator, hex(10**18)])
            tx_hash = HexBytes(f.settleEpoch(ticket.settlement_epoch).transact({"from": operator, "gas": 1_000_000}))
            assert_transaction_success_with_explanation(self.web3, tx_hash)
        finally:
            make_anvil_custom_rpc_request(self.web3, "anvil_stopImpersonatingAccount", [operator])
        after = self.get_redemption_request_status(ticket)
        if after is not AsyncVaultRequestStatus.claimable:
            raise UnsupportedVaultSimulation("Atoma settlement did not advance the ticket", unsupported_reason="atoma_settlement_incomplete")
        return VaultForcedSettlementResult(ticket, True, before, after, transaction_hashes=(tx_hash,))

    def fetch_vault_flow_events(self, hypersync_client: Any, start_block: int, end_block: int, *, owner: HexAddress | None = None, settlement_epoch: int | None = None) -> Iterator[PendingVaultFlow]:
        """Discover canonical requests, optionally narrowed before receipt reads.

        Reconciliation filters indexed owner and epoch fields first so unrelated
        historical implementations cannot block recovery or multiply RPC work.

        :param hypersync_client: Configured indexed client.
        :param start_block: Inclusive first block.
        :param end_block: Inclusive final block.
        :param owner: Optional custody-owner filter.
        :param settlement_epoch: Optional aggregate-claim epoch filter.
        :return: Persistable discovery hints, not current claim statuses.
        """
        logs = fetch_vault_flow_logs_hypersync(hypersync_client=hypersync_client, vault_address=self.vault.address, topic0_list=[get_topic_signature_from_event(self.vault.vault_contract.events.WithdrawalRequested)], start_block=start_block, end_block=end_block)
        seen: set[str] = set()
        for log in tqdm(logs, desc="Atoma request receipts"):
            if owner is not None and (len(log.topics) < 2 or not log.topics[1] or log.topics[1][-40:].lower() != owner[2:].lower()):
                continue
            if settlement_epoch is not None:
                data = HexBytes(log.data)
                if len(data) != 96:
                    raise self._unavailable("malformed_indexed_request", owner, "reconciliation")
                if int.from_bytes(data[64:96], "big") != settlement_epoch:
                    continue
            identity = f"{log.transaction_hash}:{log.log_index}"
            if identity in seen:
                continue
            seen.add(identity)
            receipt = self.web3.eth.get_transaction_receipt(HexBytes(log.transaction_hash))
            if receipt["status"] != 1:
                raise ValueError("Indexed Atoma request did not succeed")
            events = self._receipt_events(self.vault.vault_contract.events.WithdrawalRequested, receipt)
            matches = [event for event in events if event["logIndex"] == log.log_index and receipt["blockNumber"] == log.block_number]
            if len(matches) != 1:
                raise ValueError("Indexed Atoma request is not canonical")
            ticket = self._ticket_from_event(receipt, matches[0])
            yield create_pending_vault_flow(chain_id=ticket.chain_id, vault_address=ticket.vault_address, owner=ticket.owner, controller=ticket.owner, direction=VaultFlowDirection.redeem, status=AsyncVaultRequestStatus.pending, log=log, ticket_data=self.serialize_redemption_ticket(ticket), request_id=ticket.settlement_epoch, settlement_id=ticket.settlement_epoch, raw_shares=ticket.raw_shares)

    def reconcile_redemption_ticket(self, ticket: AtomaRedemptionTicket, hypersync_client: Any) -> AtomaRedemptionTicket:
        """Recover the complete owner/epoch aggregate without allocating its payout.

        The caller must atomically supersede all component tickets, including
        unattributed external requests, before acknowledging the returned ticket.

        :param ticket: One known component of the epoch claim.
        :param hypersync_client: Configured indexed client for this chain.
        :return: Aggregate ticket requiring explicit consumer acknowledgement.
        """
        self._validate_ticket(ticket, canonical=True)
        block = self.web3.eth.block_number
        self.vault.check_execution_version(block)
        if int(run_vault_flow_log_fetch(hypersync_client.get_height())) - 1 < block:
            raise self._unavailable("indexer_behind_chain", ticket.owner, "reconciliation")
        matches = [self.reconstruct_redemption_ticket(flow.ticket_data) for flow in self.fetch_vault_flow_events(hypersync_client, 0, block, owner=ticket.owner, settlement_epoch=ticket.settlement_epoch)]
        if not matches or any(t.reconciliation_required for t in matches):
            raise self._unavailable("request_history_incomplete", ticket.owner, "reconciliation")
        parts = tuple(p for t in matches for p in t.parts)
        if not {p.identity for p in ticket.parts}.issubset({p.identity for p in parts}):
            raise self._unavailable("request_history_incomplete", ticket.owner, "reconciliation")
        raw = self.vault.vault_contract.functions.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=block)
        if raw <= 0 or sum(p.raw_shares for p in parts) != raw:
            raise self._unavailable("request_history_mismatch", ticket.owner, "reconciliation")
        return replace(matches[0], raw_shares=raw, parts=parts, reconciliation_required=True)

    def acknowledge_redemption_reconciliation(self, ticket: AtomaRedemptionTicket, request_ids: set[str]) -> AtomaRedemptionTicket:
        """Acknowledge all contributions before atomically persisting supersession.

        This library does not own the consumer's trade ledger. Persist the
        returned ticket and mark every listed component superseded in one write
        before broadcasting a claim; never credit each component independently.

        :param ticket: Reconciled aggregate ticket.
        :param request_ids: All request-part identities accepted by the consumer.
        :return: Claim-enabled replacement ticket.
        """
        self._validate_ticket(ticket, canonical=True)
        if request_ids != {part.identity for part in ticket.parts}:
            raise ValueError("All Atoma request contributions must be acknowledged")
        block = self.web3.eth.block_number
        implementation = self.vault.check_execution_version(block)
        if ticket.preflight_implementation != ticket.implementation or ticket.implementation != implementation:
            raise self._unavailable("implementation_reconciliation_required", ticket.owner, "reconciliation")
        for part in ticket.parts:
            _, observed = self.vault.fetch_receipt_contract(part.block_number)
            if observed != implementation:
                raise self._unavailable("implementation_reconciliation_required", ticket.owner, "reconciliation")
        if self.vault.vault_contract.functions.userEpochShares(ticket.settlement_epoch, ticket.owner).call(block_identifier=block) != ticket.raw_shares:
            raise self._unavailable("request_history_mismatch", ticket.owner, "reconciliation")
        return replace(ticket, reconciliation_required=False)

    def fetch_pending_redemption_value(self, ticket: AtomaRedemptionTicket, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Replace burned claimable shares with their validated net entitlement.

        Pending escrowed shares retain ordinary share valuation. Consumed or
        aggregate requests must be recovered before publishing a valuation.

        :param ticket: Single-request Atoma ticket.
        :param block_identifier: Consistent valuation block.
        :return: Claimable net denomination amount, or the normal share fallback.
        """
        assert not ticket.reconciliation_required and len(ticket.parts) == 1, "Atoma reconciliation or aggregate claims are unsupported"
        status = self.get_redemption_request_status(ticket, block_identifier=block_identifier)
        if status is AsyncVaultRequestStatus.none:
            raise self._unavailable("Recover completed Atoma claim before valuation", ticket.owner, "valuation")
        if status is AsyncVaultRequestStatus.claimable:
            _, _, net = self.fetch_claim_amounts(ticket, block_identifier=block_identifier)
            return self.vault.denomination_token.convert_to_decimals(net)
        return None

    def fetch_completed_redemption_tx_hash(self, ticket: AtomaRedemptionTicket) -> HexBytes | None:
        # Hypersync is an optional extra. Import it only for indexed recovery,
        # keeping normal Atoma/Lagoon execution usable without that package.
        try:
            from eth_defi.hypersync.utils import configure_hypersync_from_env
        except ModuleNotFoundError as exc:
            if exc.name != "hypersync":
                raise
            raise self._unavailable("hypersync_required", ticket.owner, "recovery") from exc

        self._validate_ticket(ticket, canonical=True)
        client = configure_hypersync_from_env(self.web3).hypersync_client
        if client is None:
            raise self._unavailable("hypersync_required", ticket.owner, "recovery")
        chain_block = self.web3.eth.block_number
        # Stay strictly inside the indexed archive, even when its head moves
        # while the query is being opened. A lagging archive cannot prove absence.
        indexed_block = int(run_vault_flow_log_fetch(client.get_height())) - 1
        end_block = min(chain_block, indexed_block)
        start_block = min(p.block_number for p in ticket.parts)
        if end_block < start_block:
            raise self._unavailable("indexer_behind_chain", ticket.owner, "recovery")
        logs = fetch_vault_flow_logs_hypersync(hypersync_client=client, vault_address=self.vault.address, topic0_list=[get_topic_signature_from_event(self.vault.vault_contract.events.WithdrawalClaimed)], start_block=start_block, end_block=end_block)
        candidates = {HexBytes(log.transaction_hash) for log in logs if log.topics[1] and log.topics[2] and log.topics[1][-40:].lower() == ticket.owner[2:].lower() and int(log.topics[2], 16) == ticket.settlement_epoch}
        if len(candidates) > 1:
            raise self._unavailable("ambiguous_claim_history", ticket.owner, "recovery")
        if candidates:
            tx_hash = candidates.pop()
            if not isinstance(self.analyse_redemption(tx_hash, ticket), AtomaFlowAnalysis):
                raise self._unavailable("claim_evidence_mismatch", ticket.owner, "recovery")
            return tx_hash
        if end_block < chain_block:
            raise self._unavailable("indexer_behind_chain", ticket.owner, "recovery")
        return None
