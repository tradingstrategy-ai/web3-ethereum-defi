# ruff: noqa: EM101, FBT001, FBT002, PLR0917, PLR6301
"""NaraUSD+ synchronous deposits and cooldown-based redemptions."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.provider.anvil import is_anvil, mine
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    DepositTicket,
    RedemptionRequest,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
)


@dataclass(slots=True)
class NaraRedemptionTicket(RedemptionTicket):
    """Persist a NaraUSD+ cooldown redemption request.

    The vault keeps one active cooldown per owner and does not assign request
    identifiers, so the request transaction hash provides a stable identity.
    The observed cooldown state binds the ticket to that specific request.
    """

    #: Naive UTC deadline recorded by the vault after ``cooldownShares``.
    cooldown_end: datetime.datetime

    #: Raw NaraUSD assets escrowed for this cooldown.
    raw_assets: int

    def get_request_id(self) -> int:
        """Return the request transaction hash as an integer identity.

        :return:
            Unique integer derived from the request transaction hash.
        """
        return int.from_bytes(self.tx_hash, byteorder="big")


class NaraRedemptionRequest(RedemptionRequest):
    """Parse a completed NaraUSD+ ``cooldownShares`` transaction."""

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> NaraRedemptionTicket:
        """Create a persistent ticket after the request succeeds.

        :param tx_hashes:
            Broadcast transaction hashes; the final hash is ``cooldownShares``.
        :return:
            Persistable ticket for the later ``unstake`` claim.
        """
        tx_hash = tx_hashes[-1]
        cooldown_end, raw_assets = self.vault.narausd_plus_contract.functions.cooldowns(self.owner).call()
        cooldown_end = int(cooldown_end)
        raw_assets = int(raw_assets)
        if cooldown_end == 0 or raw_assets <= 0:
            raise RuntimeError(f"NaraUSD+ cooldown state was not created for {self.owner}")
        return NaraRedemptionTicket(
            vault_address=Web3.to_checksum_address(self.vault.address),
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
            cooldown_end=datetime.datetime.fromtimestamp(cooldown_end, tz=datetime.UTC).replace(tzinfo=None),
            raw_assets=raw_assets,
        )


class NaraDepositManager(ERC4626DepositManager):
    """NaraUSD+ manager with synchronous deposits and asynchronous cooldown redemptions.

    NaraUSD+ is Nara's appreciating staking token for NaraUSD. Deposits mint
    shares immediately through the standard ERC-4626 path, but redemptions are
    asynchronous: the holder starts an owner-specific cooldown, waits for it to
    mature, then claims the underlying NaraUSD. This manager keeps the inherited
    synchronous deposit flow and replaces the redemption flow with a
    two-step cooldown/claim lifecycle tracked by :class:`NaraRedemptionTicket`.

    **Deposit process**

    Synchronous, fully inherited. After ``approve()``,
    :meth:`create_deposit_request` builds a single ERC-4626 ``deposit`` call and
    :meth:`estimate_deposit` uses the standard ``previewDeposit`` path.
    :meth:`can_create_deposit_request` gates on ``maxDeposit(owner) > 0``.

    **Redemption process**

    Asynchronous, two-step. :meth:`create_redemption_request` does *not* build a
    ``redeem`` / ``withdraw`` call — it builds a single ``cooldownShares(raw_shares)``
    call that escrows the shares and starts the owner's cooldown, returning a
    :class:`NaraRedemptionRequest`. After the request confirms,
    :meth:`NaraRedemptionRequest.parse_redeem_transaction` reads the vault's
    ``cooldowns(owner)`` state to build a persistable ticket. Once the cooldown
    matures, :meth:`finish_redemption` builds the ``unstake(to)`` claim that
    sends NaraUSD to the receiver. :meth:`has_synchronous_redemption` returns
    ``False``. The receiver may differ from the owner but cannot be the zero
    address.

    **Queues and settlement**

    Per-owner cooldown state rather than a numbered queue: the vault keeps at
    most one active cooldown per owner and assigns no request id, so the ticket
    is identified by the request transaction hash together with the observed
    ``cooldown_end`` and escrowed ``raw_assets``. Attempting a second cooldown
    while one is active raises. :meth:`get_redemption_request_status` maps live
    ``cooldowns()`` state plus the latest block timestamp to ``pending``,
    ``claimable`` or ``none`` (the last also covering a claimed, removed or
    superseded cooldown).

    **Lockups and cooldowns**

    Deposits have no lockup. Redemptions carry the live cooldown read from
    ``cooldownDuration()``: :meth:`estimate_redemption_delay` returns it, and
    :meth:`NaraVault.get_estimated_lock_up` reports the same value (currently
    seven days on Ethereum). :meth:`get_redemption_delay_over` returns the
    per-owner cooldown expiry when one exists.

    **Whitelisting / access control**

    Permissionless. No NaraUSD+-specific whitelist is applied beyond the
    inherited :meth:`check_deposit_whitelist` preflight;
    :meth:`can_create_redemption_request` simply requires a positive share
    balance and no cooldown already in progress.

    **Anvil settlement (force_settle)**

    Deposits settle in their originating transaction. Redemption settlement is
    time-based, not keeper-based: :meth:`force_settle` advances an Anvil chain
    to the ticket's ``cooldown_end`` and returns a hashless pending-to-claimable
    result. It never broadcasts ``unstake``; callers must submit the guarded
    :meth:`finish_redemption` claim themselves. A supplied local mock is
    accepted only when it is the same deployed contract as this manager's
    vault, preventing it from advancing an unrelated production ticket.
    """

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> NaraRedemptionRequest:
        """Start the owner-specific NaraUSD+ share cooldown.

        :param owner:
            NaraUSD+ share owner initiating the cooldown.
        :param to:
            Final NaraUSD receiver, defaulting to the share owner.
        :param shares:
            Decimal NaraUSD+ share amount, exclusive with ``raw_shares``.
        :param raw_shares:
            Raw NaraUSD+ share amount, exclusive with ``shares``.
        :param check_max_deposit:
            Retained inherited argument; Nara controls redemption through cooldown state.
        :param check_enough_token:
            Check the owner's current NaraUSD+ balance.
        :return:
            One-call cooldown request to settle through :meth:`finish_redemption`.
        """
        del check_max_deposit
        if (shares is None) == (raw_shares is None):
            raise ValueError("Give exactly one of shares or raw_shares")
        if to is None:
            to = owner
        if Web3.to_checksum_address(to) == Web3.to_checksum_address(ZERO_ADDRESS_STR):
            raise ValueError("NaraUSD+ redemption receiver cannot be the zero address")
        if self.is_redemption_in_progress(owner):
            raise ValueError("NaraUSD+ already has an active cooldown for this owner")

        if raw_shares is None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if raw_shares <= 0:
            raise ValueError("NaraUSD+ redemption shares must be positive")
        if check_enough_token:
            balance = int(self.vault.share_token.fetch_raw_balance_of(owner))
            if balance < raw_shares:
                raise ValueError(f"Insufficient NaraUSD+ shares: has {balance}, needs {raw_shares}")

        return NaraRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=to,
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
            funcs=[self.vault.narausd_plus_contract.functions.cooldownShares(raw_shares)],
        )

    def has_synchronous_redemption(self) -> bool:
        """Return whether NaraUSD+ redemptions settle immediately.

        :return:
            Always ``False`` because the owner must complete a cooldown first.
        """
        return False

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check whether an owner has an unclaimed NaraUSD+ cooldown.

        :param owner:
            Share owner to inspect.
        :return:
            ``True`` when the vault records a non-zero cooldown deadline.
        """
        return self.get_redemption_delay_over(owner) is not None

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Advance an Anvil Nara cooldown to the ticket's claimable deadline.

        Nara has no keeper or operator settlement transaction. Advancing the
        local Anvil clock is the complete settlement boundary, so the result
        intentionally contains no transaction hashes. The later ``unstake``
        call remains a manager-owned guarded claim.

        :param ticket:
            A pending :class:`NaraRedemptionTicket`, or ``None`` for the
            synchronous deposit no-op.
        :param mock:
            Optional local ``MockNaraVault`` bound to this exact manager vault.
            It is an identity guard only: Nara settlement is time-based and
            never calls an operator method on the supplied object.
        :param ignore_liquidity:
            Unsupported because a Nara cooldown is a time gate, not a
            redemption-liquidity gate.
        :return:
            A synchronous no-op or a pending-to-claimable, hashless cooldown
            settlement result.
        :raise UnsupportedVaultSimulation:
            If this is not Anvil, the mock is unrelated, the ticket is not a
            live Nara cooldown, or advancing time does not make it claimable.
        """
        if ignore_liquidity:
            return super().force_settle(ticket, mock=mock, ignore_liquidity=True)

        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                "NaraDepositManager.force_settle() requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem" if ticket is not None else None,
            )

        if mock is not None:
            mock_address = getattr(mock, "address", None)
            if mock_address is None or Web3.to_checksum_address(mock_address) != Web3.to_checksum_address(self.vault.address):
                raise UnsupportedVaultSimulation(
                    "Nara mock settlement must use the manager's exact vault contract",
                    unsupported_reason="mock_settlement_vault_mismatch",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    direction="redeem" if ticket is not None else None,
                )

        if ticket is None:
            return create_synchronous_settlement_result()

        if not isinstance(ticket, NaraRedemptionTicket):
            raise UnsupportedVaultSimulation(
                f"Nara force_settle requires NaraRedemptionTicket, got {type(ticket)}",
                unsupported_reason="nara_redemption_ticket_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        status_before = self.get_redemption_request_status(ticket)
        if status_before == AsyncVaultRequestStatus.none:
            raise UnsupportedVaultSimulation(
                f"Nara cooldown ticket {ticket.get_request_id()} is no longer active",
                unsupported_reason="nara_cooldown_ticket_not_active",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        if status_before == AsyncVaultRequestStatus.claimable:
            return VaultForcedSettlementResult(
                ticket=ticket,
                settlement_required=False,
                status_before=status_before,
                status_after=status_before,
            )

        cooldown_timestamp = int(ticket.cooldown_end.replace(tzinfo=datetime.UTC).timestamp())
        mine(self.web3, timestamp=cooldown_timestamp)
        status_after = self.get_redemption_request_status(ticket)
        if status_after != AsyncVaultRequestStatus.claimable:
            raise UnsupportedVaultSimulation(
                f"Nara cooldown ticket {ticket.get_request_id()} did not become claimable after advancing to {cooldown_timestamp}",
                unsupported_reason="nara_cooldown_not_claimable_after_time_advance",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        return VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=status_before,
            status_after=status_after,
        )

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Check NaraUSD+'s current ERC-4626 deposit maximum.

        :param owner:
            Prospective deposit receiver.
        :return:
            ``True`` when the current maximum is positive.
        """
        return int(self.vault.vault_contract.functions.maxDeposit(owner).call()) > 0

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Check whether the owner can start a NaraUSD+ cooldown.

        :param owner:
            Share owner to inspect.
        :return:
            ``True`` when the owner has shares and no active cooldown.
        """
        return not self.is_redemption_in_progress(owner) and int(self.vault.share_token.fetch_raw_balance_of(owner)) > 0

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Read the currently configured NaraUSD+ cooldown duration.

        :return:
            Current cooldown as a timedelta.
        """
        duration = int(self.vault.narausd_plus_contract.functions.cooldownDuration().call())
        return datetime.timedelta(seconds=duration)

    def fetch_cooldown(self, address: HexAddress | str) -> tuple[int, int]:
        """Read an owner's current NaraUSD+ cooldown state.

        :param address:
            NaraUSD+ share owner.
        :return:
            Cooldown expiry timestamp and raw escrowed NaraUSD assets.
        """
        cooldown_end, raw_assets = self.vault.narausd_plus_contract.functions.cooldowns(address).call()
        return int(cooldown_end), int(raw_assets)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Return an owner's cooldown expiry, when one exists.

        :param address:
            NaraUSD+ share owner.
        :return:
            Naive UTC cooldown expiry, or ``None`` when no claim is pending.
        """
        cooldown_end, _raw_assets = self.fetch_cooldown(address)
        if cooldown_end == 0:
            return None
        return datetime.datetime.fromtimestamp(cooldown_end, tz=datetime.UTC).replace(tzinfo=None)

    def can_finish_redeem(self, redemption_ticket: NaraRedemptionTicket) -> bool:
        """Check whether a NaraUSD+ cooldown claim can now be submitted.

        :param redemption_ticket:
            Persisted cooldown request.
        :return:
            ``True`` when the current chain timestamp has reached the deadline.
        """
        return self.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.claimable

    def reconstruct_redemption_ticket(self, data: dict) -> NaraRedemptionTicket:
        """Reconstruct a NaraUSD+ cooldown ticket after a process restart.

        :param data:
            Data produced by :meth:`serialize_redemption_ticket`.
        :return:
            NaraUSD+ cooldown ticket.
        """
        return NaraRedemptionTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            cooldown_end=datetime.datetime.fromisoformat(data["nara_cooldown_end"]),
            raw_assets=int(data["nara_raw_assets"]),
        )

    def serialize_redemption_ticket(self, ticket: NaraRedemptionTicket) -> dict:
        """Serialise a NaraUSD+ ticket with its exact cooldown identity.

        :param ticket:
            NaraUSD+ cooldown ticket.
        :return:
            JSON-compatible persistent ticket data.
        """
        data = super().serialize_redemption_ticket(ticket)
        data["nara_cooldown_end"] = ticket.cooldown_end.isoformat()
        data["nara_raw_assets"] = str(ticket.raw_assets)
        return data

    def get_redemption_request_status(self, ticket: NaraRedemptionTicket) -> AsyncVaultRequestStatus:
        """Report whether a NaraUSD+ cooldown is pending or claimable.

        :param ticket:
            NaraUSD+ cooldown ticket.
        :return:
            ``pending`` before maturity, ``claimable`` afterwards, or ``none``
            when the cooldown was claimed, removed, or superseded by another
            cooldown for the same owner.
        """
        cooldown_end, raw_assets = self.fetch_cooldown(ticket.owner)
        ticket_cooldown_end = int(ticket.cooldown_end.replace(tzinfo=datetime.UTC).timestamp())
        if cooldown_end == 0 or cooldown_end != ticket_cooldown_end or raw_assets != ticket.raw_assets:
            return AsyncVaultRequestStatus.none
        latest_timestamp = int(self.web3.eth.get_block("latest")["timestamp"])
        if latest_timestamp >= cooldown_end:
            return AsyncVaultRequestStatus.claimable
        return AsyncVaultRequestStatus.pending

    def finish_redemption(self, redemption_ticket: NaraRedemptionTicket) -> ContractFunction:
        """Build the NaraUSD+ post-cooldown claim transaction.

        :param redemption_ticket:
            Matured cooldown ticket.
        :return:
            ``unstake`` contract call that sends NaraUSD to the requested receiver.
        """
        if not self.can_finish_redeem(redemption_ticket):
            raise ValueError("NaraUSD+ cooldown is not claimable for this ticket")
        return self.vault.narausd_plus_contract.functions.unstake(redemption_ticket.to)
