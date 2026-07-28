"""Plutus Hedge asynchronous redemption manager.

The Plutus Hedge vault has been upgraded to an ERC-7540-style asynchronous
redemption contract (``HedgeVaultV2``): deposits stay synchronous, but the
standard ERC-4626 ``redeem(shares,receiver,owner)`` is disabled (it reverts
``UseRequestRedeem()`` `0x797f246a`). A redemption is instead a two-phase flow —
``requestRedeem`` escrows shares and emits ``RedeemRequested``, an operator
later ``fulfillRedeem``s the request, and the owner claims with
``redeem(requestId,receiver)``.

This manager models that flow: synchronous deposits (inherited) plus an
asynchronous redemption request/ticket/status/claim lifecycle with typed
preflight errors. On-fork operator settlement (``fulfillRedeem``) is role-gated
by OpenZeppelin AccessControl and is not reproduced here, so
``supports_anvil_settlement`` is explicitly ``False`` with a stable reason.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.events import EventLogErrorFlags

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.provider.anvil import is_anvil
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    CannotParseRedemptionTransaction,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
)

#: ``UseRequestRedeem()`` — standard ERC-4626 ``redeem`` is disabled; use the
#: asynchronous request flow. ``keccak("UseRequestRedeem()")[:4]``.
USE_REQUEST_REDEEM_SELECTOR = HexBytes("0x797f246a")

#: ``WithdrawalsArePaused()`` custom-error selector.
WITHDRAWALS_ARE_PAUSED_SELECTOR = HexBytes("0xe14e66da")

#: Plutus fulfilment is role-gated and cannot be reproduced from the vault ABI.
PLUTUS_ANVIL_SETTLEMENT_UNSUPPORTED_REASON = "plutus_redeem_fulfilment_is_access_control_role_gated"


@dataclass(slots=True)
class PlutusRedemptionTicket(RedemptionTicket):
    """Persisted Plutus Hedge asynchronous redemption request.

    The ERC-7540-style ``requestId`` selects the pending/claimable getters and
    the later ``fulfillRedeem`` / claim events.
    """

    #: ERC-7540-style redemption request id from ``RedeemRequested``.
    request_id: int

    def get_request_id(self) -> int:
        """Return the redemption request id.

        :return:
            Plutus Hedge redemption request id.
        """
        return self.request_id


class PlutusRedemptionRequest(RedemptionRequest):
    """Plutus Hedge ``requestRedeem`` request, parsed from ``RedeemRequested``."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> PlutusRedemptionTicket:
        """Parse and validate the ``RedeemRequested`` receipt event.

        :param tx_hashes:
            Hashes broadcast for this request; the final hash is the request.
        :return:
            Validated Plutus redemption ticket.
        :raise CannotParseRedemptionTransaction:
            If the receipt does not contain one matching request event.
        """
        tx_hash = tx_hashes[-1]
        receipt = self.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"
        assert receipt["status"] == 1, f"Transaction reverted: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.RedeemRequested().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one RedeemRequested event, got {logs!r} at {tx_hash.hex()}")
        args = logs[0]["args"]
        if Web3.to_checksum_address(args["owner"]) != Web3.to_checksum_address(self.owner):
            raise CannotParseRedemptionTransaction(f"RedeemRequested owner mismatch: {args['owner']} != {self.owner}")
        if int(args["shares"]) != self.raw_shares:
            raise CannotParseRedemptionTransaction(f"RedeemRequested shares mismatch: {args['shares']} != {self.raw_shares}")

        return PlutusRedemptionTicket(
            vault_address=Web3.to_checksum_address(self.vault.address),
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
            request_id=int(args["requestId"]),
        )


class PlutusAsyncDepositManager(ERC4626DepositManager):
    """Plutus Hedge adapter: synchronous deposits, operator-fulfilled redemptions.

    The Plutus Hedge vault (``HedgeVaultV2``) keeps synchronous ERC-4626
    deposits but replaces synchronous redemption with an ERC-7540-style
    request/fulfil/claim flow: the standard ``redeem(shares, receiver, owner)`` is
    disabled and reverts ``UseRequestRedeem()``
    (:data:`USE_REQUEST_REDEEM_SELECTOR`).

    **Deposit process.** Synchronous. Deposits use the inherited ERC-4626
    ``deposit`` path (standard ERC-20 ``approve`` of the denomination token then
    ``deposit``); shares are minted in the same transaction. :meth:`estimate_deposit`
    prices shares through ``convertToShares`` to avoid a reverting preview, and
    :meth:`is_deposit_in_progress` always returns ``False``.

    **Redemption process.** Asynchronous, three phase. :meth:`create_redemption_request`
    builds a single ``requestRedeem(shares, owner, owner)`` call (the receiver must
    equal ``owner``), which escrows the shares and emits ``RedeemRequested``. The
    ERC-7540-style ``requestId`` is read from that event by
    :meth:`PlutusRedemptionRequest.parse_redeem_transaction` into a
    :class:`PlutusRedemptionTicket`. An operator then ``fulfillRedeem``s the
    request; once fulfilled the owner claims with ``redeem(requestId, receiver)``
    returned by :meth:`finish_redemption`. A still-pending request can be
    cancelled with ``cancelRedeemRequest`` (:meth:`reclaim_withdrawal`).

    **Queues and settlement.** Settlement is operator fulfilment, not a queue or
    epoch. Per-request state is read by ``requestId`` from
    ``pendingRedeemRequest`` / ``claimableRedeemRequest`` and mapped by
    :meth:`get_redemption_request_status` to ``pending`` (awaiting fulfilment),
    ``claimable`` (fulfilled) or ``none``. Because state is keyed by request id
    rather than owner, :meth:`is_redemption_in_progress` always returns ``False``
    and callers must track the ticket.

    **Lockups and cooldowns.** No deterministic onchain deadline — pay-out timing
    depends on when the operator fulfils the request. There is no
    :meth:`estimate_redemption_delay` override on the manager; the vault's
    :meth:`PlutusVault.get_estimated_lock_up` supplies only a modelling estimate
    of roughly one month, because Plutus vaults are opened and closed manually.

    **Whitelisting / access control.** Permissionless for depositors — Plutus
    applies no deposit whitelist. Redemption requests are refused with a typed
    :class:`~eth_defi.vault.deposit_redeem.VaultFlowUnavailable` only when
    withdrawals are paused (``withdrawalsPaused`` /
    :data:`WITHDRAWALS_ARE_PAUSED_SELECTOR`) or the owner holds too few shares.

    **Anvil settlement (force_settle).** Deferred. A ``None`` (synchronous
    deposit) ticket returns the shared no-op, but a redemption ticket cannot be
    settled on a fork: ``fulfillRedeem`` is gated by OpenZeppelin AccessControl
    and its role holder is deployment-specific and not discoverable from the vault
    interface, so :meth:`force_settle` raises
    :class:`~eth_defi.vault.deposit_redeem.UnsupportedVaultSimulation` rather than
    forging a fulfilment.
    """

    def estimate_deposit(
        self,
        owner: HexAddress | None,
        amount: Decimal,
        block_identifier: BlockIdentifier = "latest",
    ) -> Decimal:
        """Estimate shares through ``convertToShares`` to avoid a reverting preview.

        :param owner:
            Unused; the conversion is not owner-specific.
        :param amount:
            Decimal denomination amount.
        :param block_identifier:
            Block number or ``"latest"``.
        :return:
            Estimated decimal shares.
        """
        del owner
        raw_amount = self.vault.denomination_token.convert_to_raw(amount)
        raw_shares = self.vault.vault_contract.functions.convertToShares(raw_amount).call(block_identifier=block_identifier)
        return self.vault.share_token.convert_to_decimals(raw_shares)

    def has_synchronous_redemption(self) -> bool:
        """Plutus Hedge redemptions are operator-finalised, not synchronous.

        :return:
            Always ``False``.
        """
        return False

    def _withdrawals_paused(self) -> bool:
        """Read the vault's withdrawals-paused flag.

        :return:
            ``True`` when redemption requests are paused.
        """
        return bool(self.vault.vault_contract.functions.withdrawalsPaused().call())

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
        check_max_redeem=True,
    ) -> PlutusRedemptionRequest:
        """Build a Plutus Hedge ``requestRedeem`` asynchronous redemption request.

        :param owner:
            Share owner and controller.
        :param to:
            Final receiver; must equal ``owner``.
        :param shares:
            Decimal shares, exclusive with ``raw_shares``.
        :param raw_shares:
            Raw shares, exclusive with ``shares``.
        :param check_max_deposit:
            Unused inherited parameter.
        :param check_enough_token:
            Check the owner's current share balance.
        :param check_max_redeem:
            Unused; capacity is enforced at fulfilment.
        :return:
            One-call asynchronous redemption request.
        :raise VaultFlowUnavailable:
            When withdrawals are paused.
        """
        del check_max_deposit, check_max_redeem
        assert raw_shares or shares, "Either raw_shares or shares must be supplied"
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) != Web3.to_checksum_address(owner):
            raise ValueError("Plutus redemptions must return assets to their share owner")
        if raw_shares is None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if raw_shares <= 0:
            raise ValueError("Plutus redemption shares must be positive")

        if self._withdrawals_paused():
            raise VaultFlowUnavailable(
                f"Plutus Hedge withdrawals are paused for vault {self.vault.address} on chain {self.vault.chain_id}",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="WithdrawalsArePaused",
                preflight_result="redemption_paused",
                error_selector=WITHDRAWALS_ARE_PAUSED_SELECTOR,
            )

        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise VaultFlowUnavailable(
                    f"Insufficient Plutus Hedge shares for vault {self.vault.address} on chain {self.vault.chain_id}: has {balance}, needs {raw_shares}",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    preflight_result="redemption_unavailable",
                    requested_raw_amount=raw_shares,
                    available_raw_amount=balance,
                )

        return PlutusRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=[self.vault.vault_contract.functions.requestRedeem(raw_shares, owner, owner)],
        )

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Plutus deposits are synchronous.

        :param owner:
            Ignored.
        :return:
            Always ``False``.
        """
        del owner
        return False

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Plutus tracks requests per id, not per owner; use the ticket instead.

        :param owner:
            Ignored; ``requestId``-scoped status is available via
            :meth:`get_redemption_request_status`.
        :return:
            Always ``False``.
        """
        del owner
        return False

    def can_finish_redeem(self, redemption_ticket: PlutusRedemptionTicket) -> bool:
        """Return whether the request has been fulfilled and is claimable.

        :param redemption_ticket:
            Plutus redemption ticket.
        :return:
            ``True`` when the request has a claimable balance.
        """
        assert isinstance(redemption_ticket, PlutusRedemptionTicket)
        return int(self.vault.vault_contract.functions.claimableRedeemRequest(redemption_ticket.request_id, redemption_ticket.owner).call()) > 0

    def finish_redemption(self, redemption_ticket: PlutusRedemptionTicket):
        """Build the ``redeem(requestId, receiver)`` claim call.

        :param redemption_ticket:
            Fulfilled Plutus redemption ticket.
        :return:
            Bound claim call.
        """
        assert isinstance(redemption_ticket, PlutusRedemptionTicket)
        return self.vault.vault_contract.functions.redeem(redemption_ticket.request_id, redemption_ticket.to)

    def get_redemption_request_status(self, ticket: PlutusRedemptionTicket) -> AsyncVaultRequestStatus:
        """Map the request's pending/claimable balances to a status.

        :param ticket:
            Plutus redemption ticket.
        :return:
            ``claimable`` when fulfilled, ``pending`` while awaiting fulfilment,
            otherwise ``none``.
        """
        assert isinstance(ticket, PlutusRedemptionTicket)
        contract = self.vault.vault_contract
        if int(contract.functions.claimableRedeemRequest(ticket.request_id, ticket.owner).call()) > 0:
            return AsyncVaultRequestStatus.claimable
        if int(contract.functions.pendingRedeemRequest(ticket.request_id, ticket.owner).call()) > 0:
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def serialize_redemption_ticket(self, ticket: PlutusRedemptionTicket) -> dict:
        """Serialise the Plutus request id alongside the base ticket data.

        :param ticket:
            Ticket to persist across a restart.
        :return:
            JSON-compatible base and Plutus-specific ticket fields.
        """
        assert isinstance(ticket, PlutusRedemptionTicket)
        data = super().serialize_redemption_ticket(ticket)
        data["plutus_request_id"] = ticket.request_id
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> PlutusRedemptionTicket:
        """Rebuild a Plutus redemption ticket from serialised data.

        :param data:
            JSON-compatible data from :meth:`serialize_redemption_ticket`.
        :return:
            Reconstructed ticket.
        """
        return PlutusRedemptionTicket(
            vault_address=Web3.to_checksum_address(data["vault_address"]),
            owner=Web3.to_checksum_address(data["vault_owner"]),
            to=Web3.to_checksum_address(data.get("vault_to", data["vault_owner"])),
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            request_id=int(data["plutus_request_id"]),
        )

    def reclaim_withdrawal(self, redemption_ticket: PlutusRedemptionTicket):
        """Build the ``cancelRedeemRequest`` reclaim call for a pending request.

        :param redemption_ticket:
            Pending Plutus redemption ticket.
        :return:
            Bound cancel call.
        """
        assert isinstance(redemption_ticket, PlutusRedemptionTicket)
        return self.vault.vault_contract.functions.cancelRedeemRequest(redemption_ticket.request_id)

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Report that Plutus operator fulfilment is not reproducible on a fork.

        Plutus ``fulfillRedeem`` is gated by OpenZeppelin AccessControl; the
        fulfilment role and its holder are deployment-specific and not
        discoverable from the vault interface alone, so the request cannot be
        safely progressed to claimable on a fork. Synchronous ``None`` calls
        return the shared no-op.

        :param ticket:
            Pending redemption ticket, or ``None``.
        :param mock:
            A deployed ``MockPlutusVault`` only for local mock tests. Its
            ``fulfillRedeem`` call replaces the role-gated production operator
            action; no production fork settlement is attempted.
        :param ignore_liquidity:
            Unsupported because Plutus fulfilment is an operator boundary, not
            a local immediate-liquidity gate.
        :return:
            No-op result for ``None``.
        :raise UnsupportedVaultSimulation:
            For a redemption ticket, because operator fulfilment cannot be
            reproduced without role discovery.
        """
        if ignore_liquidity:
            return super().force_settle(ticket, mock=mock, ignore_liquidity=True)

        if ticket is None:
            return create_synchronous_settlement_result()
        if mock is not None:
            assert isinstance(ticket, PlutusRedemptionTicket), f"Plutus mock settlement requires PlutusRedemptionTicket, got {type(ticket)}"
            if not is_anvil(self.web3):
                raise UnsupportedVaultSimulation("Plutus mock settlement requires an Anvil provider", unsupported_reason="anvil_provider_required")
            tx_hash = mock.functions.fulfillRedeem(ticket.request_id).transact({"from": self.web3.eth.accounts[0]})
            return VaultForcedSettlementResult(
                ticket=ticket,
                settlement_required=True,
                status_before=AsyncVaultRequestStatus.pending,
                status_after=AsyncVaultRequestStatus.claimable,
                transaction_hashes=(HexBytes(tx_hash),),
            )
        raise UnsupportedVaultSimulation(
            f"Plutus Hedge fulfilment is role-gated (AccessControl) and not reproducible on a fork for vault {self.vault.address}",
            unsupported_reason=PLUTUS_ANVIL_SETTLEMENT_UNSUPPORTED_REASON,
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            direction="redeem",
        )
