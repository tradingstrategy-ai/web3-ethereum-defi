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

from eth_defi.abi import ZERO_ADDRESS_STR, get_topic_signature_from_event
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    RedemptionRequest,
    RedemptionTicket,
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

    Supported simulation path: standard ERC-4626 deposits complete
    immediately and use the shared ``force_settle(None)`` Anvil no-op.

    Known limitations: redemptions depend on the live strategy's valuation and
    liquidity checks. This manager has no safe generic Anvil settlement driver
    for an Accountable redemption ticket, so ``force_settle(ticket)`` raises
    :class:`UnsupportedVaultSimulation`. Multiple concurrent controller
    requests, partial claims, repeated settlement rounds and delegated
    controllers are likewise unsupported.
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
        if raw_amount is None:
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        if raw_amount <= 0:
            raise ValueError("Accountable deposit amount must be positive")
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
            raise ValueError(f"Accountable redemption shares {raw_shares} are below minimum {minimum}")
        if self.is_redemption_in_progress(owner):
            raise ValueError("Accountable has a pending or claimable redemption for this controller")
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise ValueError(f"Insufficient Accountable shares: has {balance}, needs {raw_shares}")
        return AccountableRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=[self.vault.vault_contract.functions.requestRedeem(raw_shares, owner, owner)],
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
