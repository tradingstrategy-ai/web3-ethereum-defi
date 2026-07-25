"""Ember synchronous-deposit and operator-finalised redemption support.

Ember deposits follow ERC-4626 ``deposit(uint256,address)`` semantics, while
withdrawals use ``redeemShares(uint256,address)`` and are later paid directly
to the requested receiver by the vault operator. See the `Ember vault
contracts <https://github.com/ember-protocol/Ember-Vaults-EVM>`__.
"""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import eth_abi
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract.contract import ContractFunction

from eth_defi.abi import ZERO_ADDRESS_STR, get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.provider.anvil import is_anvil, make_anvil_custom_rpc_request
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
    VaultForcedSettlementResult,
)
from eth_defi.vault.flow_events import (
    PendingVaultFlow,
    VaultFlowDirection,
    create_pending_vault_flow,
    decode_indexed_event_address,
    event_data_to_bytes,
    fetch_vault_flow_logs_hypersync,
)

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault

#: Safety cap on operator settlement batches when draining Ember's global
#: withdrawal queue on an Anvil fork, so a stuck queue fails loudly.
EMBER_MAX_SETTLEMENT_BATCHES = 50


@dataclass(slots=True)
class EmberRedemptionTicket(RedemptionTicket):
    """Persisted Ember withdrawal request.

    Ember's globally monotonic request sequence identifies the later
    ``RequestProcessed`` event. The request block bound makes the terminal log
    lookup efficient and makes restart-safe persistence possible.
    """

    #: Globally monotonic Ember withdrawal request sequence.
    request_sequence_number: int

    #: Block that emitted ``RequestRedeemed``.
    block_number: int

    #: Naive UTC timestamp of :attr:`block_number`.
    block_timestamp: datetime.datetime

    def get_request_id(self) -> int:
        """Return Ember's globally monotonic withdrawal request identifier.

        :return:
            Request sequence number used by the operator processing event.
        """
        return self.request_sequence_number


class EmberRedemptionRequest(RedemptionRequest):
    """Two-call Ember redemption request: share approval and queue creation."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> EmberRedemptionTicket:
        """Parse and validate the ``RequestRedeemed`` receipt event.

        The event is emitted by the Ember vault even when the outer transaction
        is a GuardV0 SimpleVault or Lagoon module call. Its timestamp is
        required to equal the receipt block timestamp at millisecond precision.

        :param tx_hashes:
            Hashes broadcast for this request; the final hash is redemption.
        :return:
            Validated, restart-safe Ember redemption ticket.
        :raise CannotParseRedemptionTransaction:
            If the receipt does not contain one matching Ember request event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"
        assert receipt["status"] == 1, f"Transaction reverted: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.RequestRedeemed().process_receipt(
            receipt,
            errors=EventLogErrorFlags.Discard,
        )
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RequestRedeemed event, got {logs!r} at {tx_hash.hex()}")

        args = logs[0]["args"]
        vault_address = Web3.to_checksum_address(self.vault.address)
        if Web3.to_checksum_address(args["vault"]) != vault_address:
            raise CannotParseRedemptionTransaction(f"RequestRedeemed vault mismatch: {args['vault']} != {vault_address}")
        if Web3.to_checksum_address(args["owner"]) != Web3.to_checksum_address(self.owner):
            raise CannotParseRedemptionTransaction(f"RequestRedeemed owner mismatch: {args['owner']} != {self.owner}")
        if Web3.to_checksum_address(args["receiver"]) != Web3.to_checksum_address(self.to):
            raise CannotParseRedemptionTransaction(f"RequestRedeemed receiver mismatch: {args['receiver']} != {self.to}")
        if int(args["shares"]) != self.raw_shares:
            raise CannotParseRedemptionTransaction(f"RequestRedeemed shares mismatch: {args['shares']} != {self.raw_shares}")

        block_number = int(receipt["blockNumber"])
        block_timestamp = get_block_timestamp(self.web3, block_number)
        event_timestamp_ms = int(args["timestamp"])
        block_timestamp_seconds = int(block_timestamp.replace(tzinfo=datetime.UTC).timestamp())
        if event_timestamp_ms // 1_000 != block_timestamp_seconds:
            raise CannotParseRedemptionTransaction(f"RequestRedeemed timestamp mismatch: {event_timestamp_ms} ms != {block_timestamp_seconds} s")

        return EmberRedemptionTicket(
            vault_address=vault_address,
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
            request_sequence_number=int(args["sequenceNumber"]),
            block_number=block_number,
            block_timestamp=block_timestamp,
        )


class EmberDepositManager(ERC4626DepositManager):
    """Ember adapter with synchronous deposits and operator-finalised redemptions.

    Ember pairs an ordinary ERC-4626 deposit path with a custom, operator-driven
    withdrawal queue. Deposits mint shares immediately, whereas withdrawals only
    escrow shares onchain and are paid out later by the vault operator, so the
    depositor never owns a claim step of their own. See the `Ember vault
    contracts <https://github.com/ember-protocol/Ember-Vaults-EVM>`__.

    **Deposit process.** Synchronous. :meth:`create_deposit_request` builds a
    single standard ERC-4626 ``deposit(assets, receiver)`` call (via
    :func:`~eth_defi.erc_4626.flow.deposit_4626`), preceded by the usual ERC-20
    ``approve`` of the denomination token onto the vault. The receiver is
    explicit and defaults to ``owner``; the zero address is rejected. Shares are
    minted in the same transaction, which emits Ember's ``VaultDeposit`` event
    (not the ERC-4626 ``Deposit`` event) parsed by :meth:`analyse_deposit`.

    **Redemption process.** Asynchronous. Ember does not use ERC-4626
    ``redeem``. :meth:`create_redemption_request` builds two calls: a self
    ``approve(vault, shares)`` followed by the custom
    ``redeemShares(shares, receiver)``, which escrows the shares and enqueues the
    request. The request id is Ember's globally monotonic ``sequenceNumber``,
    read from the ``RequestRedeemed`` event by
    :meth:`EmberRedemptionRequest.parse_redeem_transaction` and persisted in an
    :class:`EmberRedemptionTicket` together with the request block. There is no
    depositor claim call — the operator pays the receiver directly, so
    :meth:`can_finish_redeem` and :meth:`finish_redemption` always report that no
    depositor-owned finish call exists.

    **Queues and settlement.** Withdrawal requests accumulate in a single
    vault-global queue (not per owner). The vault operator — read at runtime from
    the ``roles()`` tuple ``(admin, operator, rateManager)`` — drains it by
    calling ``processWithdrawalRequests(n)``, which emits a terminal
    ``RequestProcessed`` event per request (carrying ``skipped``/``cancelled``
    flags). ``minWithdrawableShares`` enforces a per-request minimum and
    ``pauseStatus`` gates both deposits and withdrawal requests.

    **Lockups and cooldowns.** No deterministic onchain deadline exists: pay-out
    timing is entirely operator-driven. :meth:`get_redemption_delay_over`
    therefore returns ``None``, and :meth:`estimate_redemption_delay` is only a
    service-level estimate from :meth:`EmberVault.get_estimated_lock_up`, which
    reads Ember's offchain ``withdrawal_period_days`` metadata and falls back to
    four days.

    **Whitelisting / access control.** Permissionless — Ember has no deposit
    whitelist. :meth:`can_create_deposit_request` only checks that deposits are
    unpaused and ``maxDeposit(owner) > 0``, and :meth:`can_create_redemption_request`
    only checks that withdrawals are unpaused and the owner holds at least
    ``minWithdrawableShares``.

    **Anvil settlement (force_settle).** :meth:`force_settle` requires an Anvil
    provider. It impersonates the ``roles()`` operator, funds it, then drains the
    global queue with repeated ``processWithdrawalRequests`` calls (capped at
    :data:`EMBER_MAX_SETTLEMENT_BATCHES`) until the ticket's sequence leaves the
    owner's pending list. Because there is no claim step, a settled request ends
    in :attr:`AsyncVaultRequestStatus.none` rather than ``claimable``; success is
    defined as a matching, non-skipped, non-cancelled ``RequestProcessed`` event.
    A still-pending, skipped or cancelled outcome raises
    :class:`UnsupportedVaultSimulation`.
    """

    def __init__(self, vault: "EmberVault"):
        """Create an Ember manager for a protocol-specific Ember vault.

        :param vault:
            Ember vault adapter whose ABI exposes the custom withdrawal queue.
        """
        self.vault = vault

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC4626DepositRequest:
        """Build one synchronous Ember deposit call with an explicit receiver.

        Ember uses standard ERC-4626 deposits but emits ``VaultDeposit`` rather
        than ``Deposit``. The generic constructor otherwise remains suitable.

        :param owner:
            Address funding the denomination token transfer.
        :param to:
            Share receiver; defaults to ``owner``.
        :param amount:
            Decimal denomination amount, mutually exclusive with raw amount.
        :param raw_amount:
            Raw denomination amount, mutually exclusive with decimal amount.
        :param check_max_deposit:
            Check the vault's current ERC-4626 maximum when requested.
        :param check_enough_token:
            Check the owner's current denomination balance when requested.
        :return:
            One-call synchronous deposit request.
        """
        if (amount is None) == (raw_amount is None):
            raise ValueError("Give exactly one of amount or raw_amount")
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("Ember deposit receiver cannot be the zero address")

        if raw_amount is None:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        if raw_amount <= 0:
            raise ValueError("Ember deposit amount must be positive")

        func = deposit_4626(
            self.vault,
            owner,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
            receiver=to,
        )
        return ERC4626DepositRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            funcs=[func],
            amount=self.vault.denomination_token.convert_to_decimals(raw_amount),
            raw_amount=raw_amount,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> EmberRedemptionRequest:
        """Build Ember share approval followed by ``redeemShares``.

        Ember does not use ERC-4626 ``redeem``. It escrows shares after a
        self-allowance and lets its operator transfer the final assets later.

        :param owner:
            Owner of Ember vault shares.
        :param to:
            Final asset receiver; defaults to ``owner``.
        :param shares:
            Decimal shares, mutually exclusive with raw shares.
        :param raw_shares:
            Raw shares, mutually exclusive with decimal shares.
        :param check_max_deposit:
            Retained inherited API argument; Ember applies its own queue rules.
        :param check_enough_token:
            Validate the owner's current share balance before binding calls.
        :return:
            Two-call Ember request in approval then redemption order.
        """
        del check_max_deposit
        if (shares is None) == (raw_shares is None):
            raise ValueError("Give exactly one of shares or raw_shares")
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("Ember redemption receiver cannot be the zero address")

        if raw_shares is None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if raw_shares <= 0:
            raise ValueError("Ember redemption shares must be positive")
        if self._withdrawals_paused():
            raise ValueError("Ember withdrawals are paused")

        minimum = int(self.vault.vault_contract.functions.minWithdrawableShares().call())
        if raw_shares < minimum:
            raise ValueError(f"Ember redemption shares {raw_shares} are below minimum {minimum}")
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise ValueError(f"Insufficient Ember shares: has {balance}, needs {raw_shares}")

        return EmberRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=[
                self.vault.share_token.contract.functions.approve(self.vault.address, raw_shares),
                self.vault.vault_contract.functions.redeemShares(raw_shares, to),
            ],
        )

    def has_synchronous_deposit(self) -> bool:
        """Return whether Ember deposits finish in their submitted transaction.

        :return:
            Always ``True`` for Ember deposits.
        """
        return True

    def has_synchronous_redemption(self) -> bool:
        """Return whether Ember redemptions finish in their submitted transaction.

        :return:
            Always ``False`` because an operator later processes the request.
        """
        return False

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Return whether Ember has an asynchronous deposit queue.

        :param owner:
            Ignored; Ember deposits are synchronous.
        :return:
            Always ``False``.
        """
        del owner
        return False

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check the owner-specific Ember pending withdrawal amount.

        ``getAccountState(owner)`` returns the owner's
        ``totalPendingWithdrawalShares`` as its first ABI-named value, followed
        by that same owner's pending sequence list. This is not a vault-global
        pending-share getter.

        :param owner:
            Ember share owner to inspect.
        :return:
            ``True`` when this owner has pending withdrawal shares.
        """
        total_pending_withdrawal_shares, _pending_sequences, _cancel_sequences = self.vault.vault_contract.functions.getAccountState(owner).call()
        return int(total_pending_withdrawal_shares) > 0

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Check whether Ember currently advertises deposit availability.

        The inherited API has no amount parameter, so this is not a cap or
        fillability guarantee. The actual deposit call still enforces the
        requested amount and current remaining cap.

        :param owner:
            Prospective deposit receiver used for ``maxDeposit``.
        :return:
            ``True`` when deposits are unpaused and a positive maximum exists.
        """
        return not self._deposits_paused() and int(self.vault.vault_contract.functions.maxDeposit(owner).call()) > 0

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Check current Ember withdrawal queue availability for an owner.

        :param owner:
            Owner whose share balance is checked.
        :return:
            ``True`` when withdrawals are unpaused and the owner can redeem at
            least Ember's minimum withdrawal share amount.
        """
        if self._withdrawals_paused():
            return False
        minimum = int(self.vault.vault_contract.functions.minWithdrawableShares().call())
        balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
        return balance >= minimum

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Return Ember's off-chain operator service estimate.

        The value comes from :meth:`EmberVault.get_estimated_lock_up`, which
        reads Ember's cached off-chain metadata and uses its documented four-day
        fallback. It is not an on-chain processing deadline.

        :return:
            Documented estimated operator processing interval.
        """
        estimate = self.vault.get_estimated_lock_up()
        assert estimate is not None, "EmberVault provides a four-day fallback estimate"
        return estimate

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Return no deadline because Ember processing is operator-driven.

        :param address:
            Ignored Ember requester address.
        :return:
            Always ``None`` because no deterministic on-chain deadline exists.
        """
        del address
        return None

    def can_finish_redeem(self, redemption_ticket: EmberRedemptionTicket) -> bool:
        """Report that an Ember depositor never owns a final claim call.

        :param redemption_ticket:
            Ember ticket whose settlement is operator-finalised.
        :return:
            Always ``False``.
        """
        assert isinstance(redemption_ticket, EmberRedemptionTicket)
        return False

    def finish_redemption(self, redemption_ticket: EmberRedemptionTicket) -> ContractFunction | None:
        """Return no call because only Ember's operator may process withdrawals.

        :param redemption_ticket:
            Ember ticket retained for type validation.
        :return:
            Always ``None``; never an operator-only processing call.
        """
        assert isinstance(redemption_ticket, EmberRedemptionTicket)
        return None

    def force_settle(self, ticket: DepositTicket | RedemptionTicket | None) -> VaultForcedSettlementResult:
        """Drive an Ember redemption request to its terminal state on Anvil.

        Ember withdrawals are paid by the vault operator's
        ``processWithdrawalRequests`` call, not by the depositor, so a fork
        simulation must impersonate that operator. The operator address is read
        from the vault's onchain ``roles()`` tuple (``(admin, operator,
        rateManager)``) rather than hardcoded, then the withdrawal queue is
        drained until the ticket's request sequence is processed.

        **Terminal-state semantics.** Ember has no depositor claim step. A
        processed request leaves the owner's pending list, so
        :meth:`get_redemption_request_status` returns
        :attr:`AsyncVaultRequestStatus.none` — *not* ``claimable``. Success is
        therefore defined as "a non-skipped, non-cancelled ``RequestProcessed``
        event exists for this exact ticket", verified through
        :meth:`analyse_redemption`.

        :param ticket:
            Pending :class:`EmberRedemptionTicket`, or ``None`` for the
            synchronous-deposit no-op.
        :return:
            Settlement outcome with before/after status and operator
            transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil, or the request cannot be moved to a
            successful terminal state (still pending, skipped or cancelled).
        """
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation("EmberDepositManager.force_settle() requires an Anvil provider")

        if ticket is None:
            # Ember deposits are synchronous; nothing to settle.
            return VaultForcedSettlementResult(
                ticket=None,
                settlement_required=False,
                status_before=None,
                status_after=None,
            )

        assert isinstance(ticket, EmberRedemptionTicket), f"Ember force_settle requires EmberRedemptionTicket, got {type(ticket)}"

        status_before = self.get_redemption_request_status(ticket)
        if status_before != AsyncVaultRequestStatus.pending:
            # Already processed or cancelled; no operator action can progress it.
            return VaultForcedSettlementResult(
                ticket=ticket,
                settlement_required=False,
                status_before=status_before,
                status_after=status_before,
            )

        operator = Web3.to_checksum_address(self.vault.vault_contract.functions.roles().call()[1])

        make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [operator])
        make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [operator, hex(10**18)])

        transaction_hashes: list[HexBytes] = []
        try:
            # The withdrawal queue is global (not per-owner). Drain it in
            # batches until this ticket's sequence leaves the owner's pending
            # list, with a cap so a stuck queue fails loudly instead of looping.
            for _ in range(EMBER_MAX_SETTLEMENT_BATCHES):
                pending_length = int(self.vault.vault_contract.functions.getPendingWithdrawalsLength().call())
                if pending_length == 0:
                    break
                process_hash = self.vault.vault_contract.functions.processWithdrawalRequests(pending_length).transact({"from": operator})
                assert_transaction_success_with_explanation(self.web3, process_hash)
                transaction_hashes.append(HexBytes(process_hash))
                if self.get_redemption_request_status(ticket) != AsyncVaultRequestStatus.pending:
                    break
        finally:
            make_anvil_custom_rpc_request(self.web3, "anvil_stopImpersonatingAccount", [operator])

        status_after = self.get_redemption_request_status(ticket)
        completion_hash = self.fetch_completed_redemption_tx_hash(ticket)
        if status_after == AsyncVaultRequestStatus.pending or completion_hash is None:
            raise UnsupportedVaultSimulation(f"Ember settlement did not process request sequence {ticket.request_sequence_number} for vault {self.vault.address} on chain {self.vault.chain_id}: {status_before} -> {status_after}")

        # Reject a skipped/cancelled terminal event: the operator handled the
        # request but did not pay it, so the redemption did not settle.
        analysis = self.analyse_redemption(completion_hash, ticket)
        if isinstance(analysis, DepositRedeemEventFailure):
            raise UnsupportedVaultSimulation(f"Ember settlement processed request sequence {ticket.request_sequence_number} for vault {self.vault.address} without payment: {analysis.revert_reason}")

        return VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=status_before,
            status_after=status_after,
            transaction_hashes=tuple(transaction_hashes),
        )

    def get_redemption_request_status(self, ticket: EmberRedemptionTicket) -> AsyncVaultRequestStatus:
        """Map the exact Ember request sequence to pending or consumed state.

        :param ticket:
            Persisted Ember redemption request.
        :return:
            ``pending`` while its sequence remains in the owner list, otherwise
            ``none``. ``none`` is not evidence of successful payment.
        """
        assert isinstance(ticket, EmberRedemptionTicket)
        _total_pending, pending_sequences, _cancel_sequences = self.vault.vault_contract.functions.getAccountState(ticket.owner).call()
        if ticket.request_sequence_number in {int(sequence) for sequence in pending_sequences}:
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def fetch_completed_redemption_tx_hash(self, ticket: RedemptionTicket) -> HexBytes | None:
        """Locate and validate the operator ``RequestProcessed`` transaction.

        The globally monotonic request sequence selects a candidate event within
        the ticket's request-block-to-tip range. Owner, receiver and shares are
        then validated rather than used as filters, preventing a malformed ABI
        decode from being silently converted into a false ``None``.

        :param ticket:
            Ember redemption ticket whose terminal event is sought.
        :return:
            Unique matching operator transaction, or ``None`` before observed.
        :raise ValueError:
            If a matching sequence has inconsistent identity or duplicates.
        """
        assert isinstance(ticket, EmberRedemptionTicket)
        event = self.vault.vault_contract.events.RequestProcessed
        logs = self.web3.eth.get_logs(
            {
                "address": self.vault.address,
                "topics": [[get_topic_signature_from_event(event)]],
                "fromBlock": ticket.block_number,
                "toBlock": int(self.web3.eth.block_number),
            }
        )
        matches = []
        for log in logs:
            decoded = event().process_log(log)
            if int(decoded["args"]["requestSequenceNumber"]) == ticket.request_sequence_number:
                matches.append(decoded)

        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError(f"Found {len(matches)} RequestProcessed events for Ember sequence {ticket.request_sequence_number}")

        args = matches[0]["args"]
        self._validate_processed_event(ticket, args)
        return HexBytes(matches[0]["transactionHash"])

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse the actual Ember ``VaultDeposit`` receipt event.

        :param claim_tx_hash:
            Mined direct, GuardV0 or Lagoon-module deposit transaction.
        :param deposit_ticket:
            Expected request details when available.
        :return:
            Executed deposit amounts or a transaction failure result.
        """
        tx_hash = HexBytes(claim_tx_hash)
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason="Ember deposit transaction reverted")

        logs = self.vault.vault_contract.events.VaultDeposit().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one VaultDeposit event, got {logs!r} at {tx_hash.hex()}")
        args = logs[0]["args"]
        if Web3.to_checksum_address(args["vault"]) != Web3.to_checksum_address(self.vault.address):
            raise CannotParseRedemptionTransaction("VaultDeposit vault does not match Ember adapter")
        if deposit_ticket is not None:
            if Web3.to_checksum_address(args["depositor"]) != Web3.to_checksum_address(deposit_ticket.owner):
                raise CannotParseRedemptionTransaction("VaultDeposit depositor does not match deposit ticket")
            if Web3.to_checksum_address(args["receiver"]) != Web3.to_checksum_address(deposit_ticket.to):
                raise CannotParseRedemptionTransaction("VaultDeposit receiver does not match deposit ticket")

        block_number = int(receipt["blockNumber"])
        return DepositRedeemEventAnalysis(
            from_=Web3.to_checksum_address(args["depositor"]),
            to=Web3.to_checksum_address(args["receiver"]),
            denomination_amount=self.vault.denomination_token.convert_to_decimals(int(args["amountDeposited"])),
            share_count=self.vault.share_token.convert_to_decimals(int(args["sharesMinted"])),
            tx_hash=tx_hash,
            block_number=block_number,
            block_timestamp=get_block_timestamp(self.web3, block_number),
        )

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse one terminal Ember operator processing event.

        :param claim_tx_hash:
            Operator transaction hash returned by completion lookup.
        :param redemption_ticket:
            Ember ticket used to select and validate the terminal event.
        :return:
            Executed payout, or skipped/cancelled terminal failure evidence.
        """
        assert isinstance(redemption_ticket, EmberRedemptionTicket), "Ember redemption analysis requires EmberRedemptionTicket"
        tx_hash = HexBytes(claim_tx_hash)
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason="Ember operator processing transaction reverted")

        decoded_logs = self.vault.vault_contract.events.RequestProcessed().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        matches = [log for log in decoded_logs if int(log["args"]["requestSequenceNumber"]) == redemption_ticket.request_sequence_number]
        if len(matches) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RequestProcessed event for sequence {redemption_ticket.request_sequence_number}, got {matches!r}")
        args = matches[0]["args"]
        self._validate_processed_event(redemption_ticket, args)
        if args["skipped"] or args["cancelled"]:
            reasons = []
            if args["skipped"]:
                reasons.append("skipped")
            if args["cancelled"]:
                reasons.append("cancelled")
            return DepositRedeemEventFailure(tx_hash=tx_hash, revert_reason=f"Ember redemption request {' and '.join(reasons)}")

        block_number = int(receipt["blockNumber"])
        return DepositRedeemEventAnalysis(
            from_=Web3.to_checksum_address(args["owner"]),
            to=Web3.to_checksum_address(args["receiver"]),
            denomination_amount=self.vault.denomination_token.convert_to_decimals(int(args["withdrawAmount"])),
            share_count=self.vault.share_token.convert_to_decimals(int(args["shares"])),
            tx_hash=tx_hash,
            block_number=block_number,
            block_timestamp=get_block_timestamp(self.web3, block_number),
        )

    def serialize_redemption_ticket(self, ticket: EmberRedemptionTicket) -> dict:
        """Serialise Ember's sequence and canonical request-block identity.

        :param ticket:
            Ember ticket to persist across a process restart.
        :return:
            JSON-compatible base and Ember-specific ticket fields.
        """
        assert isinstance(ticket, EmberRedemptionTicket)
        data = super().serialize_redemption_ticket(ticket)
        data.update(
            {
                "ember_request_sequence_number": ticket.request_sequence_number,
                "ember_request_block_number": ticket.block_number,
                "ember_request_block_timestamp": ticket.block_timestamp.isoformat(),
            }
        )
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> EmberRedemptionTicket:
        """Rebuild an Ember ticket from its serialised request identity.

        :param data:
            JSON-compatible data returned by :meth:`serialize_redemption_ticket`.
        :return:
            Ticket ready for current-state and terminal-event checks.
        """
        return EmberRedemptionTicket(
            vault_address=Web3.to_checksum_address(data["vault_address"]),
            owner=Web3.to_checksum_address(data["vault_owner"]),
            to=Web3.to_checksum_address(data.get("vault_to", data["vault_owner"])),
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            request_sequence_number=int(data["ember_request_sequence_number"]),
            block_number=int(data["ember_request_block_number"]),
            block_timestamp=datetime.datetime.fromisoformat(data["ember_request_block_timestamp"]),
        )

    def fetch_vault_flow_events(
        self,
        hypersync_client,
        start_block: int,
        end_block: int,
    ) -> Iterator[PendingVaultFlow]:
        """Fetch historical Ember ``RequestRedeemed`` queue requests.

        :param hypersync_client:
            Configured Ethereum Hypersync client.
        :param start_block:
            Inclusive request-event block range start.
        :param end_block:
            Inclusive request-event block range end.
        :return:
            Event-derived pending redemption discovery hints in chain order.
        """
        event = self.vault.vault_contract.events.RequestRedeemed
        topic = get_topic_signature_from_event(event).lower()
        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=self.vault.address,
            topic0_list=[topic],
            start_block=start_block,
            end_block=end_block,
        )
        chain_id = int(self.web3.eth.chain_id)
        vault_address = Web3.to_checksum_address(self.vault.address)
        for log in logs:
            owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
            receiver = Web3.to_checksum_address(decode_indexed_event_address(log.topics[3]))
            raw_shares, event_timestamp_ms, _total_shares, _pending_to_burn, sequence_number = eth_abi.decode(
                ["uint256", "uint256", "uint256", "uint256", "uint256"],
                event_data_to_bytes(log.data),
            )
            raw_shares = int(raw_shares)
            sequence_number = int(sequence_number)
            block_timestamp = log.block_timestamp
            if block_timestamp is None:
                block_timestamp = datetime.datetime.fromtimestamp(int(event_timestamp_ms) / 1_000, tz=datetime.UTC).replace(tzinfo=None)
            if int(event_timestamp_ms) // 1_000 != int(block_timestamp.replace(tzinfo=datetime.UTC).timestamp()):
                raise ValueError(f"RequestRedeemed timestamp mismatch for Ember sequence {sequence_number}")
            ticket = EmberRedemptionTicket(
                vault_address=vault_address,
                owner=owner,
                to=receiver,
                raw_shares=raw_shares,
                tx_hash=HexBytes(log.transaction_hash),
                request_sequence_number=sequence_number,
                block_number=log.block_number,
                block_timestamp=block_timestamp,
            )
            yield create_pending_vault_flow(
                chain_id=chain_id,
                vault_address=vault_address,
                owner=owner,
                controller=owner,
                direction=VaultFlowDirection.redeem,
                status=AsyncVaultRequestStatus.pending,
                request_id=sequence_number,
                raw_assets=None,
                raw_shares=raw_shares,
                log=log,
                ticket_data=self.serialize_redemption_ticket(ticket),
            )

    def _deposits_paused(self) -> bool:
        """Read Ember's deposits pause flag.

        :return:
            ``True`` when the vault blocks new deposits.
        """
        deposits_paused, _withdrawals_paused, _privileged_paused = self.vault.vault_contract.functions.pauseStatus().call()
        return bool(deposits_paused)

    def _withdrawals_paused(self) -> bool:
        """Read Ember's withdrawal-request pause flag.

        :return:
            ``True`` when the vault blocks ``redeemShares`` requests.
        """
        _deposits_paused, withdrawals_paused, _privileged_paused = self.vault.vault_contract.functions.pauseStatus().call()
        return bool(withdrawals_paused)

    @staticmethod
    def _validate_processed_event(ticket: EmberRedemptionTicket, args) -> None:
        """Validate terminal event identity against an Ember ticket.

        :param ticket:
            Persisted Ember request identity.
        :param args:
            Decoded ``RequestProcessed`` event arguments from packaged ABI.
        :raise ValueError:
            If sequence, owner, receiver or shares disagree.
        """
        if int(args["requestSequenceNumber"]) != ticket.request_sequence_number:
            raise ValueError("Ember RequestProcessed sequence does not match ticket")
        if Web3.to_checksum_address(args["owner"]) != Web3.to_checksum_address(ticket.owner):
            raise ValueError("Ember RequestProcessed owner does not match ticket")
        if Web3.to_checksum_address(args["receiver"]) != Web3.to_checksum_address(ticket.to):
            raise ValueError("Ember RequestProcessed receiver does not match ticket")
        if int(args["shares"]) != ticket.raw_shares:
            raise ValueError("Ember RequestProcessed shares do not match ticket")
