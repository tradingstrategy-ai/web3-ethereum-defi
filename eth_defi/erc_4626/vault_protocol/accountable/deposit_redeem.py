"""Accountable synchronous deposits and asynchronous redemption claims.

Accountable vaults use standard ERC-4626 ``deposit`` calls.  Redemptions are
requested with ``requestRedeem`` and later become claimable through the normal
ERC-4626 ``redeem`` entry point.  The contract exposes aggregate pending and
claimable share balances per controller, rather than an independently
claimable balance per request id.
"""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import eth_abi
from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract.contract import ContractFunction
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, MismatchedABI

from eth_defi.abi import ZERO_ADDRESS_STR, get_deployed_contract, get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.provider.anvil import is_anvil
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    WhitelistingRequired,
    create_synchronous_settlement_result,
)
from eth_defi.vault.flow_events import (
    PendingVaultFlow,
    VaultFlowDirection,
    create_pending_vault_flow,
    decode_indexed_event_address,
    decode_indexed_event_uint,
    event_data_to_bytes,
    fetch_vault_flow_logs_hypersync,
)

if TYPE_CHECKING:
    import hypersync


#: ``InsufficientAmount()`` from the verified AccountableAsyncRedeemVault ABI.
ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR = HexBytes("0x5945ea56")

#: Accountable redemption settlement requires a strategy-operator action.
ACCOUNTABLE_ANVIL_SETTLEMENT_UNSUPPORTED_REASON = "accountable_redemption_settlement_is_strategy_operator_controlled"


@dataclass(slots=True)
class AccountableRedemptionTicket(RedemptionTicket):
    """Persisted Accountable redemption request identity.

    Accountable's later claimability getters are controller aggregates.  The
    request id is retained for audit and event discovery. The public manager
    auto-claims only self-controlled tickets to their owner because the
    contract exposes controller-level, rather than request-level, balances.
    """

    #: Queue id emitted by ``RedeemRequest``. Zero denotes instant fulfilment.
    request_id: int

    #: ERC-7540 controller that owns the aggregate pending and claimable state.
    controller: HexAddress

    #: Block that emitted the request event.
    block_number: int

    #: Naive UTC timestamp of the request block.
    block_timestamp: datetime.datetime

    def get_request_id(self) -> int:
        """Return the Accountable queue id.

        :return:
            Request id emitted by the vault.
        """
        return self.request_id


class AccountableRedemptionRequest(RedemptionRequest):
    """Parse one Accountable ``requestRedeem`` transaction."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> AccountableRedemptionTicket:
        """Parse the exact ``RedeemRequest`` event emitted by the request.

        :param tx_hashes:
            Broadcast transaction hashes; the final item is the request call.
        :return:
            Restart-safe Accountable redemption ticket.
        :raise CannotParseRedemptionTransaction:
            If the receipt does not contain exactly one matching request event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"
        assert receipt["status"] == 1, f"Transaction reverted: {tx_hash.hex()}"
        logs = self.vault.vault_contract.events.RedeemRequest().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RedeemRequest event, got {logs!r} at {tx_hash.hex()}")

        args = logs[0]["args"]
        if Web3.to_checksum_address(args["controller"]) != Web3.to_checksum_address(self.owner):
            raise CannotParseRedemptionTransaction("RedeemRequest controller does not match request owner")
        if Web3.to_checksum_address(args["owner"]) != Web3.to_checksum_address(self.owner):
            raise CannotParseRedemptionTransaction("RedeemRequest owner does not match request owner")
        # The Accountable ABI inherits ERC-7540's event field name but this
        # contract writes the requested *shares* into ``assets``.
        if int(args["assets"]) != self.raw_shares:
            raise CannotParseRedemptionTransaction("RedeemRequest assets field does not match requested shares")

        block_number = int(receipt["blockNumber"])
        return AccountableRedemptionTicket(
            vault_address=Web3.to_checksum_address(self.vault.address),
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
            request_id=int(args["requestId"]),
            controller=Web3.to_checksum_address(args["controller"]),
            block_number=block_number,
            block_timestamp=get_block_timestamp(self.web3, block_number),
        )


class AccountableDepositManager(ERC4626DepositManager):
    """Accountable adapter with synchronous deposits and claimed redemptions.

    Accountable Capital vaults are ERC-4626 vaults whose capital is deployed into
    an external strategy. Deposits settle immediately, but redemptions follow the
    ERC-7540 async pattern: a ``requestRedeem`` escrows the shares into a queue,
    the strategy operator settles it, and the owner later claims the settled
    assets through the standard ``redeem`` entry point. The contract exposes only
    controller-level aggregate pending/claimable balances, not per-request
    balances, which shapes several limitations below.

    **Deposit process**

    Fully synchronous ERC-4626. :meth:`create_deposit_request` builds a single
    ``deposit(assets, receiver)`` call (via
    :func:`~eth_defi.erc_4626.flow.deposit_4626`) after enforcing a minimum and a
    capacity check. The binding minimum is the greater of the vault-level
    ``MIN_AMOUNT_WEI`` and the strategy's per-loan ``loan().minDeposit``
    (:meth:`_fetch_strategy_loan_min_deposit`; open-term strategies revert
    ``InsufficientAmount()`` (``0x5945ea56``) inside ``strategy.onDeposit`` for a
    deposit that clears only the vault minimum). A sub-minimum or over-``maxDeposit``
    request raises :class:`VaultFlowUnavailable`. Estimation uses
    ``convertToShares`` because this deployment makes ``previewDeposit`` revert.

    **Redemption process**

    Asynchronous. :meth:`create_redemption_request` builds a single
    ``requestRedeem(shares, owner, owner)`` call; the owner acts as its own
    ERC-7540 controller and no share-token allowance is needed because
    ``requestRedeem`` itself escrows the shares. The receiver must equal the
    owner. The vault-level ``MIN_AMOUNT_WEI`` (in shares) is enforced; the
    strategy ``minRedeem`` is deliberately **not** applied because its unit is
    unconfirmed for this deployment. Because claimability is a controller
    aggregate, an existing pending or claimable request blocks a further request
    for the same owner (:meth:`is_redemption_in_progress`). Settled shares are
    claimed with :meth:`finish_redemption`, which calls ``redeem`` for the
    current claimable amount.

    **Queues and settlement**

    ERC-7540-style queue. Pending and claimable state is read as **controller
    aggregates** through ``pendingRedeemRequest(0, controller)`` and
    ``claimableRedeemRequest(0, controller)`` (:meth:`_pending_redeem_shares` /
    :meth:`_claimable_redeem_shares`), not per request id. Settlement is
    performed off-band by the strategy operator; timing is not deterministic. The
    manager only auto-claims self-controlled tickets back to their share owner —
    it never directs an aggregate claim to a custom receiver or auto-claims a
    delegated-controller ticket. Multiple concurrent controller requests, partial
    claims and repeated settlement rounds are not modelled beyond claiming the
    current aggregate.

    **Lockups and cooldowns**

    No deterministic window. :meth:`estimate_redemption_delay` returns zero and
    :meth:`get_redemption_delay_over` returns ``None`` because settlement timing
    is strategy-controlled; :meth:`AccountableVault.get_estimated_lock_up` is
    likewise ``None``.

    **Whitelisting / access control**

    Accountable exposes a constructor-selected vault-wide ``permissionLevel``. Mode
    ``None`` is permissionless, ``KYC`` requires signed per-call authorisation,
    and ``Whitelist`` checks persistent ``allowed(address)`` membership. The
    manager performs this admission check independently from minimum amount,
    loan state, and ``maxDeposit(owner)`` capacity. Hyperithm uses ``None``.

    **Anvil settlement (force_settle)**

    Deposits use the shared ``force_settle(None)`` no-op. Redemptions have no safe
    generic Anvil settlement driver, so ``force_settle(ticket)`` raises
    :class:`~eth_defi.vault.deposit_redeem.UnsupportedVaultSimulation`;
    settlement must be driven by the real strategy operator.
    """

    def estimate_deposit(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Estimate Accountable shares without calling ``previewDeposit``.

        The reported Hyperithm deployment rejects the generic ERC-4626
        preview call even though its conversion function remains available.
        ``convertToShares`` is the value used by the contract's synchronous
        deposit path and gives callers a non-reverting estimate.

        :param owner:
            Deposit owner. Accountable's conversion is owner-independent.
        :param amount:
            Denomination-token amount to deposit.
        :param block_identifier:
            Block number or ``"latest"``.
        :return:
            Estimated decimal share amount.
        :raise ValueError:
            If the vault reports a zero share estimate.
        """
        del owner
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        if raw_shares <= 0:
            raise ValueError(f"Accountable deposit estimate is zero for {amount} {self.vault.denomination_token.symbol}")
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def _fetch_strategy_loan_min_deposit(self) -> int | None:
        """Read the Accountable strategy's per-loan minimum deposit, if any.

        Accountable vaults delegate deposits to a strategy contract
        (``strategy()``). Open-term strategies enforce a per-loan
        ``loan().minDeposit`` (in denomination-asset raw units, the same unit as
        a deposit ``raw_amount``) that is typically far above the vault-level
        ``MIN_AMOUNT_WEI``; a deposit below it reverts ``InsufficientAmount()``
        (`0x5945ea56`) inside ``strategy.onDeposit`` rather than at the vault.

        Strategy variants without per-loan terms (the base
        ``AccountableStrategy``) do not expose ``loan()``; those and any read
        failure yield ``None`` so the caller falls back to the vault-level
        minimum instead of blocking.

        :return:
            Raw minimum deposit in denomination-asset units, or ``None`` when
            the strategy exposes no per-loan minimum.
        """
        try:
            strategy_address = self.vault.vault_contract.functions.strategy().call()
        except (ABIFunctionNotFound, MismatchedABI, ContractLogicError, BadFunctionCallOutput, ValueError):
            return None
        if not strategy_address or int(strategy_address, 16) == 0:
            return None
        strategy = get_deployed_contract(self.web3, "accountable/OpenTermCompoundV1.json", strategy_address)
        try:
            loan = strategy.functions.loan().call()
        except (ABIFunctionNotFound, MismatchedABI, ContractLogicError, BadFunctionCallOutput, ValueError):
            # Strategy variant without per-loan terms.
            return None
        # loan() tuple: (minDeposit, minRedeem, maxCapacity, ...).
        return int(loan[0])

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC4626DepositRequest:
        """Build one standard ERC-4626 deposit with an explicit receiver.

        :param owner: Address funding denomination tokens.
        :param to: Share receiver. Defaults to ``owner``.
        :param amount: Decimal denomination amount, exclusive with raw amount.
        :param raw_amount: Raw denomination amount, exclusive with amount.
        :param check_max_deposit: Check the vault's current ERC-4626 maximum.
        :param check_enough_token: Check the owner's token balance.
        :return: One-call synchronous deposit request.
        """
        if (amount is None) == (raw_amount is None):
            raise ValueError("Give exactly one of amount or raw_amount")
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("Accountable deposit receiver cannot be the zero address")
        permission_level = self.vault.fetch_permission_level()
        accounts = {Web3.to_checksum_address(owner), Web3.to_checksum_address(to)}
        for account in accounts:
            if not self.vault.is_account_whitelisted(account, permission_level):
                raise WhitelistingRequired(
                    f"Depositor {account} is not admitted for Accountable vault {self.vault.address} on chain {self.vault.chain_id}",
                    protocol="Accountable",
                    vault_address=self.vault.address,
                    caller=account,
                    direction="deposit",
                    phase="preflight",
                )
        if raw_amount is None:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        if raw_amount <= 0:
            raise ValueError("Accountable deposit amount must be positive")
        minimum = self.vault.fetch_minimum_raw_deposit()
        if minimum is None:
            minimum = 0
        if raw_amount < minimum:
            raise VaultFlowUnavailable(
                f"Accountable deposit amount {raw_amount} is below minimum {minimum}",
                protocol="Accountable",
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
                decoded_error="InsufficientAmount",
                preflight_result="below_minimum",
                requested_raw_amount=raw_amount,
                minimum_raw_amount=minimum,
                error_selector=ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR,
            )
        if check_max_deposit:
            max_deposit = int(self.vault.vault_contract.functions.maxDeposit(to).call())
            if raw_amount > max_deposit:
                reason = "Accountable deposit exceeds current admission capacity"
                raise VaultFlowUnavailable(
                    reason,
                    protocol="Accountable",
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="deposit",
                    phase="preflight",
                    preflight_result="deposit_closed",
                    requested_raw_amount=raw_amount,
                    available_raw_amount=max_deposit,
                )
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
    ) -> AccountableRedemptionRequest:
        """Build a request that transfers shares into Accountable's queue.

        Accountable uses owner as controller and does not require a share-token
        allowance: ``requestRedeem`` itself escrows the shares.  Its aggregate
        getters make two concurrent tickets ambiguous, so an existing pending
        or claimable request blocks a further request for this owner.

        :param owner: Share owner and Accountable controller.
        :param to: Final denomination receiver, which must be ``owner``.
        :param shares: Decimal shares, exclusive with raw shares.
        :param raw_shares: Raw shares, exclusive with shares.
        :param check_max_deposit: Retained inherited API parameter; unused.
        :param check_enough_token: Check the owner's share balance.
        :return: One-call redemption request.
        """
        del check_max_deposit
        if (shares is None) == (raw_shares is None):
            raise ValueError("Give exactly one of shares or raw_shares")
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("Accountable redemption receiver cannot be the zero address")
        if Web3.to_checksum_address(to) != Web3.to_checksum_address(owner):
            raise ValueError("Accountable redemptions must return assets to their share owner")
        if raw_shares is None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if raw_shares <= 0:
            raise ValueError("Accountable redemption shares must be positive")
        minimum = int(self.vault.vault_contract.functions.MIN_AMOUNT_WEI().call())
        if raw_shares < minimum:
            # Strategy-level minRedeem is intentionally not applied here: its
            # unit (shares vs assets) is not confirmed for this deployment, and
            # a mis-scaled comparison would false-block. The vault-level
            # minimum is unit-correct (shares) and is surfaced as a typed error.
            raise VaultFlowUnavailable(
                f"Accountable redemption shares {raw_shares} are below minimum {minimum}",
                protocol="Accountable",
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="InsufficientAmount",
                preflight_result="below_minimum",
                requested_raw_amount=raw_shares,
                minimum_raw_amount=minimum,
                error_selector=ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR,
            )
        if self.is_redemption_in_progress(owner):
            raise VaultFlowUnavailable(
                "Accountable has a pending or claimable redemption for this controller",
                protocol="Accountable",
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="RedemptionPending",
                preflight_result="redemption_unavailable",
            )
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise VaultFlowUnavailable(
                    f"Insufficient Accountable shares: has {balance}, needs {raw_shares}",
                    protocol="Accountable",
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    decoded_error="InsufficientShares",
                    preflight_result="redemption_unavailable",
                    requested_raw_amount=raw_shares,
                    available_raw_amount=balance,
                )
        return AccountableRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=[self.vault.vault_contract.functions.requestRedeem(raw_shares, owner, owner)],
        )

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Refuse Accountable asynchronous settlement before any fork broadcast.

        The selected deposit direction is synchronous and retains the base
        no-op result. Accountable redemption settlement is controlled by a
        strategy operator and does not expose a safe Anvil driver.

        :param ticket:
            ``None`` for a synchronous deposit, or an Accountable redemption
            ticket to refuse.
        :param mock:
            A deployed ``MockERC7540Vault`` only for local mock tests. Its
            ``fulfillRedeemRequest`` call stands in for the strategy operator.
        :param ignore_liquidity:
            Unsupported because Accountable settlement is strategy-operator
            controlled rather than an immediate-liquidity gate.
        :return:
            Shared synchronous no-op outcome for ``None``.
        :raise UnsupportedVaultSimulation:
            For an asynchronous redemption ticket with the stable capability
            reason.
        """
        if ignore_liquidity:
            return super().force_settle(ticket, mock=mock, ignore_liquidity=True)

        if ticket is None:
            return create_synchronous_settlement_result()
        if mock is not None:
            assert isinstance(ticket, AccountableRedemptionTicket), f"Accountable mock settlement requires AccountableRedemptionTicket, got {type(ticket)}"
            if not is_anvil(self.web3):
                raise UnsupportedVaultSimulation("Accountable mock settlement requires an Anvil provider", unsupported_reason="anvil_provider_required")
            tx_hash = mock.functions.fulfillRedeemRequest(ticket.request_id).transact({"from": self.web3.eth.accounts[0]})
            return VaultForcedSettlementResult(
                ticket=ticket,
                settlement_required=True,
                status_before=AsyncVaultRequestStatus.pending,
                status_after=AsyncVaultRequestStatus.claimable,
                transaction_hashes=(HexBytes(tx_hash),),
            )
        raise UnsupportedVaultSimulation(
            f"Accountable redemption settlement is strategy-operator controlled for vault {self.vault.address} on chain {self.vault.chain_id}",
            unsupported_reason=ACCOUNTABLE_ANVIL_SETTLEMENT_UNSUPPORTED_REASON,
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            direction="redeem",
        )

    def has_synchronous_deposit(self) -> bool:
        """Return Accountable deposit completion mode.

        :return: Always ``True``.
        """
        return True

    def has_synchronous_redemption(self) -> bool:
        """Return Accountable redemption completion mode.

        :return: Always ``False`` because claims wait for settlement.
        """
        return False

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Report Accountable deposit queue state.

        :param owner: Ignored because deposits are synchronous.
        :return: Always ``False``.
        """
        del owner
        return False

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check aggregate pending or claimable shares for a controller.

        :param owner: Accountable controller address.
        :return: ``True`` when an aggregate request or claim remains.
        """
        return self._pending_redeem_shares(owner) > 0 or self._claimable_redeem_shares(owner) > 0

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return the advisory standard ERC-4626 deposit availability.

        :param owner: Prospective deposit receiver.
        :return: Whether the current maximum deposit is positive.
        """
        return int(self.vault.vault_contract.functions.maxDeposit(owner).call()) > 0

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Check that no aggregate request exists and the owner has shares.

        :param owner: Prospective controller and share owner.
        :return: ``True`` when a new request is not locally precluded.
        """
        if self.is_redemption_in_progress(owner):
            return False
        minimum = int(self.vault.vault_contract.functions.MIN_AMOUNT_WEI().call())
        return int(self.vault.share_token.fetch_raw_balance_of(owner)) >= minimum

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Return no deterministic Accountable queue deadline.

        :return: Zero duration because settlement timing is strategy controlled.
        """
        return datetime.timedelta(0)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Return no deterministic claimability deadline.

        :param address: Ignored controller address.
        :return: Always ``None``.
        """
        del address
        return None

    def get_redemption_request_status(self, ticket: AccountableRedemptionTicket) -> AsyncVaultRequestStatus:
        """Map Accountable aggregate balances to the generic request status.

        Claimability must be checked before pending status, because immediate
        settlement may leave a request id of zero and a non-zero claimable
        aggregate in the same transaction.

        :param ticket: Persisted Accountable ticket.
        :return: Claimable, pending, or absent aggregate state.
        """
        assert isinstance(ticket, AccountableRedemptionTicket)
        if self._claimable_redeem_shares(ticket.controller) > 0:
            return AsyncVaultRequestStatus.claimable
        if self._pending_redeem_shares(ticket.controller) > 0:
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def can_finish_redeem(self, redemption_ticket: AccountableRedemptionTicket) -> bool:
        """Check current aggregate claimability.

        :param redemption_ticket: Accountable request ticket.
        :return: Whether this safe self-controlled ticket has claimable shares.
        """
        return redemption_ticket.controller == redemption_ticket.owner and redemption_ticket.to == redemption_ticket.owner and self._claimable_redeem_shares(redemption_ticket.controller) > 0

    def finish_redemption(self, redemption_ticket: AccountableRedemptionTicket) -> ContractFunction:
        """Build a self-controlled claim for current claimable shares.

        Accountable exposes a controller aggregate rather than a per-request
        claim balance. The public manager therefore only claims self-controlled
        tickets back to their share owner. It never directs an aggregate claim
        to a custom receiver or auto-claims a delegated-controller ticket. A
        settlement can make only part of the ticket claimable; claim the
        current amount, then repeat after the remaining shares settle.

        :param redemption_ticket: Accountable request ticket.
        :return: Current-claimable ``redeem`` function call.
        :raise ValueError: If this ticket is delegated, has a custom receiver, or has no claimable shares.
        """
        assert isinstance(redemption_ticket, AccountableRedemptionTicket)
        if redemption_ticket.controller != redemption_ticket.owner or redemption_ticket.to != redemption_ticket.owner:
            raise ValueError("Accountable only auto-claims self-controlled redemptions to their share owner")
        claimable = self._claimable_redeem_shares(redemption_ticket.controller)
        if claimable == 0:
            raise ValueError("Accountable redemption is not claimable")
        return self.vault.vault_contract.functions.redeem(
            claimable,
            redemption_ticket.to,
            redemption_ticket.controller,
        )

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate assets using Accountable's non-reverting conversion method.

        Accountable intentionally makes ``previewRedeem`` revert, while
        ``convertToAssets`` reflects its current share price.

        :param owner: Ignored owner retained for the common manager API.
        :param shares: Decimal share amount.
        :param block_identifier: Block at which to read conversion.
        :return: Estimated denomination assets.
        """
        del owner
        raw_shares = self.vault.share_token.convert_to_raw(shares)
        raw_assets = self.vault.vault_contract.functions.convertToAssets(raw_shares).call(block_identifier=block_identifier)
        return self.vault.denomination_token.convert_to_decimals(raw_assets)

    def serialize_redemption_ticket(self, ticket: AccountableRedemptionTicket) -> dict:
        """Serialise Accountable request identity for restart-safe claims.

        :param ticket: Ticket to serialise.
        :return: JSON-compatible ticket data.
        """
        assert isinstance(ticket, AccountableRedemptionTicket)
        data = super().serialize_redemption_ticket(ticket)
        data.update(
            {
                "accountable_request_id": ticket.request_id,
                "accountable_controller": ticket.controller,
                "accountable_request_block_number": ticket.block_number,
                "accountable_request_block_timestamp": ticket.block_timestamp.isoformat(),
            }
        )
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> AccountableRedemptionTicket:
        """Rebuild an Accountable ticket from persisted data.

        :param data: JSON-compatible serialised ticket data.
        :return: Restored Accountable ticket.
        """
        return AccountableRedemptionTicket(
            vault_address=Web3.to_checksum_address(data["vault_address"]),
            owner=Web3.to_checksum_address(data["vault_owner"]),
            to=Web3.to_checksum_address(data.get("vault_to", data["vault_owner"])),
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            request_id=int(data["accountable_request_id"]),
            controller=Web3.to_checksum_address(data.get("accountable_controller", data["vault_owner"])),
            block_number=int(data["accountable_request_block_number"]),
            block_timestamp=datetime.datetime.fromisoformat(data["accountable_request_block_timestamp"]),
        )

    def fetch_vault_flow_events(
        self,
        hypersync_client: "hypersync.HypersyncClient",
        start_block: int,
        end_block: int,
    ) -> Iterator[PendingVaultFlow]:
        """Fetch historical Accountable ``RedeemRequest`` logs.

        :param hypersync_client: Configured Monad Hypersync client.
        :param start_block: Inclusive request-event range start.
        :param end_block: Inclusive request-event range end.
        :return: Event-derived pending redemption discovery hints.
        """
        event = self.vault.vault_contract.events.RedeemRequest
        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=self.vault.address,
            topic0_list=[get_topic_signature_from_event(event).lower()],
            start_block=start_block,
            end_block=end_block,
        )
        chain_id = int(self.web3.eth.chain_id)
        vault_address = Web3.to_checksum_address(self.vault.address)
        for log in logs:
            controller = Web3.to_checksum_address(decode_indexed_event_address(log.topics[1]))
            owner = Web3.to_checksum_address(decode_indexed_event_address(log.topics[2]))
            request_id = decode_indexed_event_uint(log.topics[3])
            _sender, raw_shares = eth_abi.decode(["address", "uint256"], event_data_to_bytes(log.data))
            if log.block_timestamp is None:
                raise ValueError(f"Hypersync did not provide a block timestamp for Accountable request {request_id}")
            ticket = AccountableRedemptionTicket(
                vault_address=vault_address,
                owner=owner,
                to=owner,
                raw_shares=int(raw_shares),
                tx_hash=HexBytes(log.transaction_hash),
                request_id=int(request_id),
                controller=controller,
                block_number=log.block_number,
                block_timestamp=log.block_timestamp,
            )
            yield create_pending_vault_flow(
                chain_id=chain_id,
                vault_address=vault_address,
                owner=owner,
                controller=controller,
                direction=VaultFlowDirection.redeem,
                status=AsyncVaultRequestStatus.pending,
                request_id=int(request_id),
                raw_assets=None,
                raw_shares=int(raw_shares),
                log=log,
                ticket_data=self.serialize_redemption_ticket(ticket),
            )

    def _pending_redeem_shares(self, controller: HexAddress) -> int:
        """Read the controller's aggregate pending shares.

        :param controller: Accountable controller address.
        :return: Pending share amount.
        """
        return int(self.vault.vault_contract.functions.pendingRedeemRequest(0, controller).call())

    def _claimable_redeem_shares(self, controller: HexAddress) -> int:
        """Read the controller's aggregate claimable shares.

        :param controller: Accountable controller address.
        :return: Claimable share amount.
        """
        return int(self.vault.vault_contract.functions.claimableRedeemRequest(0, controller).call())
