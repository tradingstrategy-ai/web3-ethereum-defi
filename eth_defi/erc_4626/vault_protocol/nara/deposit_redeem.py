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
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, RedemptionRequest, RedemptionTicket


@dataclass(slots=True)
class NaraRedemptionTicket(RedemptionTicket):
    """Persist a NaraUSD+ cooldown redemption request.

    The vault keeps one active cooldown per owner and does not assign request
    identifiers, so the request transaction hash provides a stable identity.
    """

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
        return NaraRedemptionTicket(
            vault_address=Web3.to_checksum_address(self.vault.address),
            owner=Web3.to_checksum_address(self.owner),
            to=Web3.to_checksum_address(self.to),
            raw_shares=self.raw_shares,
            tx_hash=HexBytes(tx_hash),
        )


class NaraDepositManager(ERC4626DepositManager):
    """NaraUSD+ manager with direct deposits and claimed cooldown redemptions."""

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
        lock_up = self.vault.get_estimated_lock_up()
        assert lock_up is not None, "NaraUSD+ exposes cooldownDuration()"
        return lock_up

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Return an owner's cooldown expiry, when one exists.

        :param address:
            NaraUSD+ share owner.
        :return:
            Naive UTC cooldown expiry, or ``None`` when no claim is pending.
        """
        cooldown_end, _pending_amount = self.vault.narausd_plus_contract.functions.cooldowns(address).call()
        if int(cooldown_end) == 0:
            return None
        return datetime.datetime.fromtimestamp(int(cooldown_end), tz=datetime.UTC).replace(tzinfo=None)

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
        )

    def get_redemption_request_status(self, ticket: NaraRedemptionTicket) -> AsyncVaultRequestStatus:
        """Report whether a NaraUSD+ cooldown is pending or claimable.

        :param ticket:
            NaraUSD+ cooldown ticket.
        :return:
            ``pending`` before maturity, ``claimable`` afterwards, or ``none``
            when no owner cooldown remains.
        """
        cooldown_end = self.get_redemption_delay_over(ticket.owner)
        if cooldown_end is None:
            return AsyncVaultRequestStatus.none
        latest_timestamp = int(self.web3.eth.get_block("latest")["timestamp"])
        if latest_timestamp >= int(cooldown_end.replace(tzinfo=datetime.UTC).timestamp()):
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
            raise ValueError("NaraUSD+ cooldown has not matured")
        return self.vault.narausd_plus_contract.functions.unstake(redemption_ticket.to)
