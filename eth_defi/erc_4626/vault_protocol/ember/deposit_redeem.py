"""Ember synchronous-deposit and operator-finalised redemption support.

Ember deposits follow ERC-4626 ``deposit(uint256,address)`` semantics, while
withdrawals use ``redeemShares(uint256,address)`` and are later paid directly
to the requested receiver by the vault operator. See the `Ember vault
contracts <https://github.com/ember-protocol/Ember-Vaults-EVM>`__.
"""

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

import eth_abi
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR, get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil, make_anvil_custom_rpc_request
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
    VaultDirectPayoutEvidence,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
    extract_revert_data,
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


#: ``InsufficientBalance()`` from Ember's operator withdrawal processor.
EMBER_INSUFFICIENT_BALANCE_SELECTOR = HexBytes("0xf4d678b8")


#: Bound Anvil-only top-ups when a deployed processor needs more than the
#: quoted FIFO withdrawal prefix.
EMBER_LIQUIDITY_TOP_UP_MAX_ATTEMPTS = 8


logger = logging.getLogger(__name__)


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

    **Anvil settlement (force_settle).** Ember has no claimable ticket state:
    its configured operator processes a request and pays the receiver directly,
    leaving the ticket in :attr:`AsyncVaultRequestStatus.none`. The Anvil driver
    therefore accepts only the configured ``roles()[1]`` operator, validates the
    matching ``RequestProcessed`` event and returns direct-payout evidence only
    when the receiver's denomination-token balance increased.
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
            raise VaultFlowUnavailable(
                "Ember withdrawals are paused",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="WithdrawalsPaused",
                preflight_result="redemption_paused",
            )

        minimum = int(self.vault.vault_contract.functions.minWithdrawableShares().call())
        if raw_shares < minimum:
            raise VaultFlowUnavailable(
                f"Ember redemption shares {raw_shares} are below minimum {minimum}",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="InsufficientAmount",
                preflight_result="below_minimum",
                requested_raw_amount=raw_shares,
                minimum_raw_amount=minimum,
            )
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise VaultFlowUnavailable(
                    f"Insufficient Ember shares: has {balance}, needs {raw_shares}",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    decoded_error="InsufficientShares",
                    preflight_result="redemption_unavailable",
                    requested_raw_amount=raw_shares,
                    available_raw_amount=balance,
                )

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

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = True,
    ) -> VaultForcedSettlementResult:
        """Process an Ember redemption through its configured Anvil operator.

        Ember uses a direct payout rather than a user claim. The driver accepts
        it as terminal only after the exact request's ``RequestProcessed``
        event and a positive denomination-token balance delta prove the payout.
        A synchronous deposit remains a no-op settlement.

        :param ticket:
            Pending :class:`EmberRedemptionTicket`, or ``None`` for the
            synchronous-deposit no-op.
        :param mock:
            A local ``MockEmberVault`` that exposes its deterministic queue for
            GuardV0 lifecycle tests. Its payout still needs the same terminal
            event and balance-delta evidence as a fork settlement.
        :param ignore_liquidity:
            On an Anvil fork, permit a synthetic denomination-token top-up for
            the settlement sources when the FIFO queue prefix cannot otherwise
            be paid. Defaults to ``True`` for simulation. Pass ``False`` to
            require the real balances.
        :return:
            Synchronous no-op or direct-payout terminal settlement result.
        :raise UnsupportedVaultSimulation:
            When the provider is not Anvil, the configured operator cannot be
            identified, the ticket is no longer in the global queue, or the
            operator transaction does not prove this ticket received a direct
            payout.
        """
        if ticket is None:
            return create_synchronous_settlement_result()

        if not isinstance(ticket, EmberRedemptionTicket):
            raise UnsupportedVaultSimulation(
                f"Ember force_settle requires EmberRedemptionTicket, got {type(ticket)}",
                unsupported_reason="anvil_settlement_ticket_unsupported",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                "Ember operator settlement requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        if mock is not None:
            return self._force_settle_mock(ticket, mock)

        status_before = self.get_redemption_request_status(ticket)
        if status_before is not AsyncVaultRequestStatus.pending:
            raise UnsupportedVaultSimulation(
                f"Ember request {ticket.get_request_id()} is {status_before.value}, not pending",
                unsupported_reason="ember_direct_payout_not_proven",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        _admin, operator, _rate_manager = self.vault.vault_contract.functions.roles().call()
        operator = Web3.to_checksum_address(operator)
        if operator == ZERO_ADDRESS_STR:
            raise UnsupportedVaultSimulation(
                f"Ember vault {self.vault.address} has no configured withdrawal operator",
                unsupported_reason="ember_operator_not_discoverable",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        pending_index = self.fetch_pending_withdrawal_index(ticket)
        denomination_token = self.vault.denomination_token
        synthetic_injected_raw = self._provision_settlement_liquidity(
            operator,
            pending_index,
            ignore_liquidity=ignore_liquidity,
        )
        process_withdrawals = self.vault.vault_contract.functions.processWithdrawalRequests(pending_index + 1)
        for attempt in range(EMBER_LIQUIDITY_TOP_UP_MAX_ATTEMPTS + 1):
            try:
                process_withdrawals.call({"from": operator})
                break
            except (ContractLogicError, ValueError) as error:
                revert_data = extract_revert_data(error)
                if revert_data is None or revert_data[:4] != EMBER_INSUFFICIENT_BALANCE_SELECTOR:
                    raise UnsupportedVaultSimulation(
                        f"Ember operator preflight reverted for request {ticket.get_request_id()}: {error}",
                        unsupported_reason="ember_operator_processing_not_reproducible",
                        protocol=self.vault.get_protocol_name(),
                        vault_address=self.vault.address,
                        direction="redeem",
                    ) from error
                if not ignore_liquidity or attempt == EMBER_LIQUIDITY_TOP_UP_MAX_ATTEMPTS:
                    raise UnsupportedVaultSimulation(
                        f"Ember settlement still lacks denomination-token liquidity to process queue through request {ticket.get_request_id()}",
                        unsupported_reason="ember_settlement_insufficient_liquidity",
                        protocol=self.vault.get_protocol_name(),
                        vault_address=self.vault.address,
                        direction="redeem",
                    ) from error

                synthetic_injected_raw += self._top_up_settlement_sources(operator)

        balance_before = denomination_token.fetch_raw_balance_of(ticket.to)
        make_anvil_custom_rpc_request(self.web3, "anvil_impersonateAccount", [operator])
        try:
            operator_balance = self.web3.eth.get_balance(operator)
            if operator_balance < 10**18:
                make_anvil_custom_rpc_request(self.web3, "anvil_setBalance", [operator, hex(10**18)])
            gas_limit = process_withdrawals.estimate_gas({"from": operator}) * 12 // 10
            tx_hash = HexBytes(process_withdrawals.transact({"from": operator, "gas": gas_limit}))
            assert_transaction_success_with_explanation(self.web3, tx_hash)
        finally:
            make_anvil_custom_rpc_request(self.web3, "anvil_stopImpersonatingAccount", [operator])

        result = self._create_direct_payout_result(ticket, status_before, tx_hash, balance_before=balance_before)
        return replace(
            result,
            synthetic_assets_injected_raw=synthetic_injected_raw,
            liquidity_constraints_ignored=synthetic_injected_raw > 0,
        )

    def _provision_settlement_liquidity(
        self,
        operator: HexAddress,
        pending_index: int,
        *,
        ignore_liquidity: bool,
    ) -> int:
        """Provision the Ember queue's possible token sources on Anvil.

        ``processWithdrawalRequests(n)`` processes every request from the queue
        head through ``n - 1``. The public queue data does not identify the
        balance debited by every deployed processor, so simulation provisions
        the vault and its verified operator without claiming live solvency.

        :param operator:
            Configured Ember withdrawal operator.
        :param pending_index:
            Zero-based index of the selected request in the global queue.
        :param ignore_liquidity:
            Whether an Anvil-only synthetic top-up may cover the shortfall.
        :return:
            Raw denomination-token amount injected into the vault and operator.
        :raise UnsupportedVaultSimulation:
            If strict mode observes an insufficient source balance.
        """
        assert is_anvil(self.web3), "Settlement provisioning is Anvil-only"
        functions = self.vault.vault_contract.functions
        # getPendingWithdrawal()[3] is the raw denomination amount.
        needed_raw = sum(int(functions.getPendingWithdrawal(index).call()[3]) for index in range(pending_index + 1))
        denomination_token = self.vault.denomination_token
        addresses = (self.vault.address, operator)
        balances = {address: denomination_token.fetch_raw_balance_of(address) for address in addresses}
        insufficient = {address: balance for address, balance in balances.items() if balance < needed_raw}
        if not insufficient:
            return 0
        if not ignore_liquidity:
            if max(balances.values()) >= needed_raw:
                return 0
            raise UnsupportedVaultSimulation(
                f"Ember settlement lacks strict fork liquidity: needs {needed_raw} raw {denomination_token.symbol}, balances={insufficient}",
                unsupported_reason="ember_settlement_insufficient_liquidity",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        injected_raw = self._top_up_settlement_sources(operator, target_raw=needed_raw)
        logger.info(
            "Ember fork settlement topped up vault %s and operator %s with %d raw %s for %d queued withdrawals",
            self.vault.address,
            operator,
            injected_raw,
            denomination_token.symbol,
            pending_index + 1,
        )
        return injected_raw

    def _top_up_settlement_sources(
        self,
        operator: HexAddress,
        *,
        target_raw: int | None = None,
    ) -> int:
        """Top up possible Ember settlement sources on an Anvil fork.

        Without ``target_raw``, double each source's observed token balance.

        :param operator:
            Configured Ember withdrawal operator.
        :param target_raw:
            Raw denomination-token balance required for each short source. When
            omitted, double the current balance of each source.
        :return:
            Raw denomination-token amount written to the fork.
        """
        denomination_token = self.vault.denomination_token
        injected_raw = 0
        for address in (self.vault.address, operator):
            current_raw = denomination_token.fetch_raw_balance_of(address)
            address_target_raw = target_raw if target_raw is not None else max(current_raw * 2, 1)
            if current_raw < address_target_raw:
                fund_erc20_on_anvil(self.web3, denomination_token.address, address, address_target_raw)
                injected_raw += address_target_raw - current_raw

        if target_raw is not None:
            return injected_raw

        logger.info(
            "Ember fork settlement doubled vault %s and operator %s liquidity by %d raw %s",
            self.vault.address,
            operator,
            injected_raw,
            denomination_token.symbol,
        )
        return injected_raw

    def _force_settle_mock(self, ticket: EmberRedemptionTicket, mock: object) -> VaultForcedSettlementResult:
        """Settle a local Ember mock while retaining production terminal checks.

        The mock retains a public ``nextRequestToProcess`` cursor.  It is used
        only to determine how many FIFO items the mock needs to process; the
        production adapter always resolves its queue index from the vault.

        :param ticket:
            Ember request selected by the mock's monotonic sequence number.
        :param mock:
            Deployed local ``MockEmberVault`` contract binding.
        :return:
            Evidence-backed terminal direct-payout settlement result.
        :raise UnsupportedVaultSimulation:
            If the ticket precedes the mock queue cursor or processing does not
            prove its direct payout.
        """
        next_request = int(mock.functions.nextRequestToProcess().call())
        request_count = ticket.request_sequence_number - next_request + 1
        if request_count <= 0:
            raise UnsupportedVaultSimulation(
                f"Ember mock request {ticket.get_request_id()} is before queue cursor {next_request}",
                unsupported_reason="ember_operator_processing_not_reproducible",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        balance_before = self.vault.denomination_token.fetch_raw_balance_of(ticket.to)
        tx_hash = HexBytes(mock.functions.processWithdrawalRequests(request_count).transact({"from": self.web3.eth.accounts[0]}))
        assert_transaction_success_with_explanation(self.web3, tx_hash)
        return self._create_direct_payout_result(ticket, AsyncVaultRequestStatus.pending, tx_hash, balance_before=balance_before)

    def _create_direct_payout_result(
        self,
        ticket: EmberRedemptionTicket,
        status_before: AsyncVaultRequestStatus,
        tx_hash: HexBytes,
        *,
        balance_before: int,
    ) -> VaultForcedSettlementResult:
        """Validate receipt and balance evidence for one direct Ember payout.

        :param ticket:
            Ember request expected in ``tx_hash``.
        :param status_before:
            Request state immediately before settlement.
        :param tx_hash:
            Operator or local mock transaction that processed the queue.
        :param balance_before:
            Receiver balance observed before the transaction.
        :return:
            Terminal direct-payout result with request-specific evidence.
        :raise UnsupportedVaultSimulation:
            If receipt, event identity, or balance delta cannot prove payout.
        """
        denomination_token = self.vault.denomination_token
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        decoded_logs = self.vault.vault_contract.events.RequestProcessed().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        matches = [log for log in decoded_logs if int(log["args"]["requestSequenceNumber"]) == ticket.request_sequence_number]
        if len(matches) != 1:
            raise UnsupportedVaultSimulation(
                f"Ember operator processing did not emit one RequestProcessed event for request {ticket.get_request_id()}",
                unsupported_reason="ember_operator_processing_not_reproducible",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        args = matches[0]["args"]
        try:
            self._validate_processed_event(ticket, args)
        except ValueError as error:
            raise UnsupportedVaultSimulation(
                f"Ember operator processing emitted an invalid event for request {ticket.get_request_id()}: {error}",
                unsupported_reason="ember_operator_processing_not_reproducible",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            ) from error
        balance_after = denomination_token.fetch_raw_balance_of(ticket.to)
        evidence = VaultDirectPayoutEvidence(
            request_id=ticket.get_request_id(),
            receiver=Web3.to_checksum_address(ticket.to),
            denomination_token=Web3.to_checksum_address(denomination_token.address),
            raw_balance_before=balance_before,
            raw_balance_after=balance_after,
            event_name="RequestProcessed",
            transaction_hash=tx_hash,
        )
        result = VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=status_before,
            status_after=AsyncVaultRequestStatus.none,
            transaction_hashes=(tx_hash,),
            direct_payout_evidence=evidence,
        )
        if args["skipped"] or args["cancelled"] or not result.is_terminal_success():
            raise UnsupportedVaultSimulation(
                f"Ember operator processing did not prove a direct payout for request {ticket.get_request_id()}",
                unsupported_reason="ember_direct_payout_not_proven",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        return result

    def fetch_pending_withdrawal_index(self, ticket: EmberRedemptionTicket) -> int:
        """Locate an Ember ticket in the vault-global pending queue.

        ``processWithdrawalRequests(n)`` consumes the first ``n`` queue items,
        rather than selecting an owner or request id.  Resolving the exact
        index before broadcasting ensures the operator transaction reaches the
        requested ticket even when older requests are pending.

        :param ticket:
            Persisted Ember redemption request to locate.
        :return:
            Zero-based queue index of ``ticket``.
        :raise UnsupportedVaultSimulation:
            If the exact request sequence is no longer pending in the global
            queue.
        """
        pending_count = int(self.vault.vault_contract.functions.getPendingWithdrawalsLength().call())
        for index in range(pending_count):
            request = self.vault.vault_contract.functions.getPendingWithdrawal(index).call()
            if int(request[5]) == ticket.request_sequence_number:
                return index

        raise UnsupportedVaultSimulation(
            f"Ember request {ticket.get_request_id()} is absent from the global pending queue",
            unsupported_reason="ember_operator_processing_not_reproducible",
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            direction="redeem",
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
        if args["skipped"] or args["cancelled"]:
            raise UnsupportedVaultSimulation(
                f"Ember request {ticket.get_request_id()} was processed without a direct payout",
                unsupported_reason="ember_direct_payout_not_proven",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
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
