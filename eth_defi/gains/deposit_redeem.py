"""Deposit and redeem flow for GToken-like vaults."""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.vault.deposit_redeem import RedemptionTicket, RedemptionRequest, CannotParseRedemptionTransaction
from hexbytes import HexBytes
from web3._utils.events import EventLogErrorFlags
from eth_typing import HexAddress

from web3.contract.contract import ContractFunction

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GainsRedemptionTicket(RedemptionTicket):
    """Gains redemption ticket details."""

    current_epoch: int
    unlock_epoch: int


class GainsRedemptionRequest(RedemptionRequest):
    """Wrap Gains makeWithdrawRequest() call.

    - Revert reason: execution reverted: `custom error 0xa73449b9`: `EndOfEpoch`

    See errors at:

    - Gains: https://www.codeslaw.app/contracts/arbitrum/0xeb754588eff264793bb80be65866d11bc8d6cbdd?tab=abi
    - Ostium: https://www.codeslaw.app/contracts/arbitrum/0x738873f37b4b4bebe3545a277a27cdac77db99cd?tab=abi
    """

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> GainsRedemptionTicket:
        """Parse the transaction receipt to get the actual shares redeemed.

        - Assumes only one redemption request per vault per transaction
        """

        # Ignore epoch request tx
        tx_hash = tx_hashes[-1]

        assert isinstance(tx_hash, HexBytes)

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.WithdrawRequested().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one WithdrawRequested event, got logs: {logs} at {tx_hash.hex()}")

        log = logs[0]

        return GainsRedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=log["args"]["shares"],
            tx_hash=tx_hash,
            current_epoch=log["args"]["currEpoch"],
            unlock_epoch=log["args"]["unlockEpoch"],
        )


class GainsDepositManager(ERC4626DepositManager):
    """Add Gains-specific redemption logic."""

    def __init__(self, vault: "eth_defi.gains.vault.GainsVault"):
        from eth_defi.gains.vault import GainsVault

        assert isinstance(vault, GainsVault), f"Got {type(vault)}"
        self.vault = vault

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Vault is always open for deposits."""
        return True

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> GainsRedemptionRequest:
        """Build a redeem transction.

        .. note::

            Withdrawal requests can only be executed in the first 2 days of each epoch.

        `Notes on Gains / Ostium withdrawals <https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5>`__.

        :param owner:
            Deposit owner.

        :param shares:
            Share amount in decimal.

            Will be converted to `raw_shares` using `share_token` decimals.

        :param raw_shares:
            Raw amount in share token
        """

        assert raw_shares or shares
        vault = self.vault

        if not raw_shares:
            raw_amount = vault.share_token.convert_to_raw(shares)
        else:
            raw_amount = raw_shares

        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        shares = vault.share_token
        block_number = self.web3.eth.block_number

        # Check we have shares
        owned_raw_amount = shares.fetch_raw_balance_of(owner, block_number)
        assert owned_raw_amount >= raw_amount, f"Cannot redeem, has only {owned_raw_amount} shares when {raw_amount} needed"

        human_amount = shares.convert_to_decimals(raw_amount)
        total_shares = vault.fetch_total_supply(block_number)
        logger.info("Setting up redemption for %s %s shares out of %s, for %s", human_amount, shares.symbol, total_shares, owner)

        # This is the underlying withdrawal request.
        # It will revert unless there is an epoch in progress
        func_1 = vault.vault_contract.functions.makeWithdrawRequest(
            raw_amount,
            owner,
        )
        return GainsRedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            shares=human_amount,
            raw_shares=raw_amount,
            funcs=[func_1],
        )

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Gains allows request redemptioon only two first dayas of three days epoch.

        :return:
            True if can create a redemption request now
        """
        return self.vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0

    def can_finish_redeem(
        self,
        redemption_ticket: GainsRedemptionTicket,
    ):
        """Check if the redemption request can be redeemed now.

        - Phase 2 of redemption, after settlement

        :param redemption_request_ticket:
            Redemption request ticket from `create_redemption_request()`

        :return:
            True if can be redeemed now
        """
        assert isinstance(redemption_ticket, GainsRedemptionTicket)
        current_epoch = self.vault.fetch_current_epoch()
        return current_epoch >= redemption_ticket.unlock_epoch

    def finish_redemption(
        self,
        redemption_ticket: GainsRedemptionTicket,
    ) -> ContractFunction:
        assert redemption_ticket.owner is not None
        assert redemption_ticket.to is not None
        return self.vault.vault_contract.functions.redeem(
            redemption_ticket.raw_shares,
            redemption_ticket.owner,
            redemption_ticket.to,
        )

    def has_synchronous_redemption(self) -> bool:
        return False

    def estimate_redemption_delay(self) -> datetime.timedelta:
        vault = self.vault
        epoch_duration_seconds = vault.open_pnl_contract.functions.requestsStart().call() + (vault.open_pnl_contract.functions.requestsEvery().call() * vault.open_pnl_contract.functions.requestsCount().call())
        return datetime.timedelta(seconds=epoch_duration_seconds)

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        contract = self.vault.vault_contract
        return contract.functions.totalSharesBeingWithdrawn(owner).call() > 0
