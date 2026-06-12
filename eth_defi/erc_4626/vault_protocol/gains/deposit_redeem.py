"""Deposit and redeem flow for GToken-like vaults.

Supports both Gains V1 (epoch-based) and Ostium V1.5 (settlement-based) flows.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.timestamp import get_block_timestamp
from eth_defi.utils import from_unix_timestamp
from eth_defi.vault.deposit_redeem import (
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositRequest,
    DepositTicket,
    RedemptionTicket,
    RedemptionRequest,
    CannotParseRedemptionTransaction,
)
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
        from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault

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


# ---------------------------------------------------------------------------
# Ostium V1.5 async settlement-based deposit/redemption
# ---------------------------------------------------------------------------

#: Arbitrum block number at which the Ostium vault was upgraded to V1.5.
#: The proxy at 0x20D419a8e12C45f88fDA7c5760bb6923Cee27F98 was upgraded
#: to implementation 0xd2619e2012a120504e043f61c8acb3ede2472bf7 in this tx:
#: https://arbiscan.io/tx/0x3f25d52219c7a9b2469ac3582c6664940ede80da361b987bad6cab6336619363
OSTIUM_V15_UPGRADE_BLOCK = 457_238_658


class OstiumSettlementFailed(Exception):
    """Raised when an Ostium V1.5 settlement resulted in RECLAIMABLE status.

    The user should call ``reclaimDeposit(settlementId)`` or
    ``reclaimWithdraw(settlementId)`` to recover their funds.
    """


#: Ostium V1.5 RequestStatus enum values
OSTIUM_REQUEST_STATUS_NONE = 0
OSTIUM_REQUEST_STATUS_PENDING = 1
OSTIUM_REQUEST_STATUS_CLAIMABLE = 2
OSTIUM_REQUEST_STATUS_RECLAIMABLE = 3


@dataclass(slots=True)
class OstiumDepositTicket(DepositTicket):
    """Tracks an in-progress Ostium V1.5 async deposit request.

    The ``settlement_id`` is extracted from the ``DepositRequestedV2`` event.
    """

    #: Settlement ID for this deposit request
    settlement_id: int


@dataclass(slots=True)
class OstiumRedemptionTicket(RedemptionTicket):
    """Tracks an in-progress Ostium V1.5 async withdrawal request.

    The ``settlement_id`` is extracted from the ``WithdrawRequestedV2`` event.
    """

    #: Settlement ID for this withdrawal request
    settlement_id: int

    def get_request_id(self) -> int:
        return self.settlement_id


class OstiumDepositRequest(DepositRequest):
    """Wraps Ostium V1.5 ``requestDeposit(uint256 assets)`` call.

    After broadcasting, parse the transaction to extract the ``settlement_id``
    from the ``DepositRequestedV2`` event.
    """

    def parse_deposit_transaction(self, tx_hashes: list[HexBytes]) -> OstiumDepositTicket:
        """Parse the ``DepositRequestedV2`` event from ``requestDeposit()`` transaction.

        ``DepositRequestedV2(address indexed owner, uint32 indexed settlementId, uint256 assets)``
        """
        tx_hash = tx_hashes[-1]
        assert isinstance(tx_hash, HexBytes)

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"
        assert receipt["status"] == 1, f"Transaction reverted: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.DepositRequestedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one DepositRequestedV2 event, got {len(logs)} at {tx_hash.hex()}")

        log = logs[0]

        gas_used = sum(self.vault.web3.eth.get_transaction_receipt(h)["gasUsed"] for h in tx_hashes)
        block_number = receipt["blockNumber"]
        block_timestamp = get_block_timestamp(self.vault.web3, block_number)

        return OstiumDepositTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_amount=self.raw_amount,
            tx_hash=tx_hash,
            gas_used=gas_used,
            block_number=block_number,
            block_timestamp=block_timestamp,
            settlement_id=log["args"]["settlementId"],
        )


class OstiumRedemptionRequest(RedemptionRequest):
    """Wraps Ostium V1.5 ``requestWithdraw(uint256 shares)`` call.

    After broadcasting, parse the transaction to extract the ``settlement_id``
    from the ``WithdrawRequestedV2`` event.
    """

    def parse_redeem_transaction(self, tx_hashes: list[HexBytes]) -> OstiumRedemptionTicket:
        """Parse the ``WithdrawRequestedV2`` event from ``requestWithdraw()`` transaction.

        ``WithdrawRequestedV2(address indexed owner, uint32 indexed settlementId, uint256 shares)``
        """
        tx_hash = tx_hashes[-1]
        assert isinstance(tx_hash, HexBytes)

        receipt = self.vault.web3.eth.get_transaction_receipt(tx_hash)
        assert receipt is not None, f"Transaction is not yet mined: {tx_hash.hex()}"
        assert receipt["status"] == 1, f"Transaction reverted: {tx_hash.hex()}"

        logs = self.vault.vault_contract.events.WithdrawRequestedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if len(logs) != 1:
            raise CannotParseRedemptionTransaction(f"Expected exactly one WithdrawRequestedV2 event, got {len(logs)} at {tx_hash.hex()}")

        log = logs[0]

        return OstiumRedemptionTicket(
            vault_address=self.vault.address,
            owner=self.owner,
            to=self.to,
            raw_shares=log["args"]["shares"],
            tx_hash=tx_hash,
            settlement_id=log["args"]["settlementId"],
        )


class OstiumV15DepositManager(ERC4626DepositManager):
    """Async deposit/redemption manager for Ostium V1.5 settlement-based flow.

    V1.5 disables ERC-4626 ``deposit()``, ``mint()``, ``withdraw()``, ``redeem()``
    and replaces them with:

    - Deposits: ``requestDeposit(assets)`` -> settlement -> ``claimDeposit(settlementId)``
    - Withdrawals: ``requestWithdraw(shares)`` -> settlement -> ``claimWithdraw(settlementId)``

    Settlement happens daily via ``tryNewSettlement()`` (public, permissionless)
    once ``maxSettlementInterval`` has elapsed.

    The ``is_deposit_in_progress()`` / ``is_redemption_in_progress()`` methods only
    check the current ``targetSettlementId``. For checking specific tickets regardless
    of the current settlement, use ``get_deposit_ticket_status()`` /
    ``get_redemption_ticket_status()``.
    """

    def __init__(self, vault: "eth_defi.erc_4626.vault_protocol.gains.vault.OstiumVault"):
        from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion

        assert isinstance(vault, OstiumVault), f"Got {type(vault)}"
        assert vault.version == OstiumVersion.v1_5, f"OstiumV15DepositManager requires V1.5 vault, got {vault.version}"
        self.vault = vault

    def has_synchronous_deposit(self) -> bool:
        return False

    def has_synchronous_redemption(self) -> bool:
        return False

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> OstiumDepositRequest:
        """Create an async deposit request via ``requestDeposit(assets)``.

        1. USDC is transferred to the vault immediately
        2. Request is queued for the next settlement
        3. After settlement, shares can be claimed via ``claimDeposit(settlementId)``

        :param owner:
            Address depositing USDC.

        :param amount:
            USDC amount in human-readable decimal.

        :param raw_amount:
            USDC amount in raw token units.
        """
        vault = self.vault

        if not raw_amount:
            assert vault.denomination_token is not None, "Vault denomination token data missing"
            raw_amount = vault.denomination_token.convert_to_raw(amount)

        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        assert to is None or to == owner, f"Ostium V1.5 requestDeposit() acts on msg.sender only, cannot specify a different receiver (to={to}, owner={owner})"

        func = vault.vault_contract.functions.requestDeposit(raw_amount)

        return OstiumDepositRequest(
            vault=vault,
            owner=owner,
            to=owner,
            funcs=[func],
            amount=amount,
            raw_amount=raw_amount,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> OstiumRedemptionRequest:
        """Create an async withdrawal request via ``requestWithdraw(shares)``.

        1. OLP shares are transferred to the vault immediately
        2. Request is queued for settlement
        3. After settlement, USDC can be claimed via ``claimWithdraw(settlementId)``

        :param owner:
            Address withdrawing shares.

        :param shares:
            Share amount in human-readable decimal.

        :param raw_shares:
            Share amount in raw token units.
        """
        assert raw_shares or shares
        vault = self.vault

        if not raw_shares:
            raw_amount = vault.share_token.convert_to_raw(shares)
        else:
            raw_amount = raw_shares

        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"

        share_token = vault.share_token
        human_amount = share_token.convert_to_decimals(raw_amount)

        assert to is None or to == owner, f"Ostium V1.5 requestWithdraw() acts on msg.sender only, cannot specify a different receiver (to={to}, owner={owner})"

        logger.info("Setting up V1.5 withdrawal request for %s %s shares, for %s", human_amount, share_token.symbol, owner)

        func = vault.vault_contract.functions.requestWithdraw(raw_amount)

        return OstiumRedemptionRequest(
            vault=vault,
            owner=owner,
            to=owner,
            shares=human_amount,
            raw_shares=raw_amount,
            funcs=[func],
        )

    def can_finish_deposit(self, deposit_ticket: OstiumDepositTicket) -> bool:
        """Check if a deposit can be claimed after settlement.

        :raises OstiumSettlementFailed:
            If the settlement resulted in RECLAIMABLE status.
            Call ``reclaim_deposit()`` to recover funds.
        """
        assert isinstance(deposit_ticket, OstiumDepositTicket)
        status = self.vault.vault_contract.functions.getDepositStatus(deposit_ticket.owner, deposit_ticket.settlement_id).call()
        if status == OSTIUM_REQUEST_STATUS_RECLAIMABLE:
            raise OstiumSettlementFailed(f"Deposit settlement {deposit_ticket.settlement_id} failed for {deposit_ticket.owner}. Call reclaimDeposit({deposit_ticket.settlement_id}) to recover funds.")
        return status == OSTIUM_REQUEST_STATUS_CLAIMABLE

    def can_finish_redeem(self, redemption_ticket: OstiumRedemptionTicket) -> bool:
        """Check if a withdrawal can be claimed after settlement.

        :raises OstiumSettlementFailed:
            If the settlement resulted in RECLAIMABLE status.
            Call ``reclaim_withdrawal()`` to recover shares.
        """
        assert isinstance(redemption_ticket, OstiumRedemptionTicket)
        status = self.vault.vault_contract.functions.getWithdrawStatus(redemption_ticket.owner, redemption_ticket.settlement_id).call()
        if status == OSTIUM_REQUEST_STATUS_RECLAIMABLE:
            raise OstiumSettlementFailed(f"Withdrawal settlement {redemption_ticket.settlement_id} failed for {redemption_ticket.owner}. Call reclaimWithdraw({redemption_ticket.settlement_id}) to recover shares.")
        return status == OSTIUM_REQUEST_STATUS_CLAIMABLE

    def finish_deposit(self, deposit_ticket: OstiumDepositTicket) -> ContractFunction:
        """Return ``claimDeposit(settlementId)`` bound function."""
        assert isinstance(deposit_ticket, OstiumDepositTicket)
        return self.vault.vault_contract.functions.claimDeposit(deposit_ticket.settlement_id)

    def finish_redemption(self, redemption_ticket: OstiumRedemptionTicket) -> ContractFunction:
        """Return ``claimWithdraw(settlementId)`` bound function."""
        assert isinstance(redemption_ticket, OstiumRedemptionTicket)
        return self.vault.vault_contract.functions.claimWithdraw(redemption_ticket.settlement_id)

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """V1.5 deposits are always accepted (caps enforced at settlement, not request)."""
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """V1.5 withdrawals can be requested any time (no epoch window restriction)."""
        return True

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check if owner has a pending deposit for the current target settlement.

        Only checks the current ``targetSettlementId(true)``. For checking a specific
        ticket's settlement, use ``get_deposit_ticket_status()``.
        """
        vault = self.vault
        target_id = vault.vault_contract.functions.targetSettlementId(True).call()
        status = vault.vault_contract.functions.getDepositStatus(owner, target_id).call()
        return status == OSTIUM_REQUEST_STATUS_PENDING

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check if owner has a pending withdrawal for the current target settlement.

        Only checks the current ``targetSettlementId(false)``. For checking a specific
        ticket's settlement, use ``get_redemption_ticket_status()``.
        """
        vault = self.vault
        target_id = vault.vault_contract.functions.targetSettlementId(False).call()
        status = vault.vault_contract.functions.getWithdrawStatus(owner, target_id).call()
        return status == OSTIUM_REQUEST_STATUS_PENDING

    def estimate_redemption_delay(self) -> datetime.timedelta:
        """Estimate how long until a withdrawal request can be claimed.

        Uses ``(targetSettlementId(false) - lastSettlementId) * maxSettlementInterval``
        to account for ``withdrawSettlementDelay``.
        """
        vault = self.vault
        target_id = vault.vault_contract.functions.targetSettlementId(False).call()
        last_id = vault.vault_contract.functions.lastSettlementId().call()
        interval = vault.vault_contract.functions.maxSettlementInterval().call()
        settlements_to_wait = max(target_id - last_id, 1)
        return datetime.timedelta(seconds=settlements_to_wait * interval)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        """Estimate when the next withdrawal settlement will occur."""
        vault = self.vault
        target_id = vault.vault_contract.functions.targetSettlementId(False).call()
        last_id = vault.vault_contract.functions.lastSettlementId().call()
        last_ts = vault.vault_contract.functions.lastSettlementTs().call()
        interval = vault.vault_contract.functions.maxSettlementInterval().call()
        settlements_to_wait = max(target_id - last_id, 1)
        return from_unix_timestamp(last_ts + settlements_to_wait * interval)

    def get_deposit_delay_over(self, address: HexAddress | str) -> datetime.datetime | None:
        """Estimate when the next deposit settlement will occur.

        Mirror of :py:meth:`get_redemption_delay_over` but for the deposit
        target settlement (``targetSettlementId(True)``).
        """
        vault = self.vault
        target_id = vault.vault_contract.functions.targetSettlementId(True).call()
        last_id = vault.vault_contract.functions.lastSettlementId().call()
        last_ts = vault.vault_contract.functions.lastSettlementTs().call()
        interval = vault.vault_contract.functions.maxSettlementInterval().call()
        settlements_to_wait = max(target_id - last_id, 1)
        return from_unix_timestamp(last_ts + settlements_to_wait * interval)

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a ``claimDeposit()`` transaction.

        Parses ``DepositClaimedV2(address indexed owner, uint32 indexed settlementId, uint256 shares)``
        and uses the settlement price for accurate denomination amount calculation.
        """
        assert isinstance(deposit_ticket, OstiumDepositTicket), f"Ostium V1.5 analyse_deposit requires OstiumDepositTicket, got {type(deposit_ticket)}"
        vault = self.vault
        receipt = vault.web3.eth.get_transaction_receipt(claim_tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=HexBytes(claim_tx_hash), revert_reason="Transaction reverted")

        logs = vault.vault_contract.events.DepositClaimedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        logs = [log for log in logs if log["address"].lower() == vault.vault_address.lower()]

        if len(logs) != 1:
            return DepositRedeemEventFailure(tx_hash=HexBytes(claim_tx_hash), revert_reason=f"Expected 1 DepositClaimedV2 event, got {len(logs)}")

        log = logs[0]
        raw_shares = log["args"]["shares"]
        settlement_id = log["args"]["settlementId"]

        # Use settlement price for accurate denomination amount
        settlement_price = vault.vault_contract.functions.settlementShareToAssetsPrice(settlement_id).call()
        raw_assets = vault.vault_contract.functions.convertToAssetsWithPrice(raw_shares, settlement_price).call()

        # Log partial refunds if the deposit was capped/scaled at settlement
        refund_logs = vault.vault_contract.events.DepositPartiallyRefunded().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        refund_logs = [log for log in refund_logs if log["address"].lower() == vault.vault_address.lower()]
        if refund_logs:
            refunded_assets = refund_logs[0]["args"]["refundedAssets"]
            logger.info(
                "Deposit partially refunded: %s raw assets returned to %s at settlement %d",
                refunded_assets,
                deposit_ticket.owner if deposit_ticket else "unknown",
                settlement_id,
            )

        share_count = vault.share_token.convert_to_decimals(raw_shares)
        denomination_amount = vault.denomination_token.convert_to_decimals(raw_assets)

        tx = vault.web3.eth.get_transaction(claim_tx_hash)
        block_number = tx["blockNumber"]
        block_timestamp = get_block_timestamp(vault.web3, block_number)

        return DepositRedeemEventAnalysis(
            from_=deposit_ticket.owner,
            to=deposit_ticket.to,
            tx_hash=HexBytes(claim_tx_hash),
            block_number=block_number,
            block_timestamp=block_timestamp,
            share_count=share_count,
            denomination_amount=denomination_amount,
        )

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a ``claimWithdraw()`` transaction.

        Parses ``WithdrawClaimedV2(address indexed owner, uint32 indexed settlementId, uint256 assets)``
        and uses the settlement price for accurate share price reconstruction.
        """
        assert isinstance(redemption_ticket, OstiumRedemptionTicket), f"Ostium V1.5 analyse_redemption requires OstiumRedemptionTicket, got {type(redemption_ticket)}"
        vault = self.vault
        receipt = vault.web3.eth.get_transaction_receipt(claim_tx_hash)

        if receipt["status"] != 1:
            return DepositRedeemEventFailure(tx_hash=HexBytes(claim_tx_hash), revert_reason="Transaction reverted")

        logs = vault.vault_contract.events.WithdrawClaimedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        logs = [log for log in logs if log["address"].lower() == vault.vault_address.lower()]

        if len(logs) != 1:
            return DepositRedeemEventFailure(tx_hash=HexBytes(claim_tx_hash), revert_reason=f"Expected 1 WithdrawClaimedV2 event, got {len(logs)}")

        log = logs[0]
        raw_assets = log["args"]["assets"]

        denomination_amount = vault.denomination_token.convert_to_decimals(raw_assets)
        share_count = vault.share_token.convert_to_decimals(redemption_ticket.raw_shares)

        tx = vault.web3.eth.get_transaction(claim_tx_hash)
        block_number = tx["blockNumber"]
        block_timestamp = get_block_timestamp(vault.web3, block_number)

        return DepositRedeemEventAnalysis(
            from_=redemption_ticket.owner,
            to=redemption_ticket.to,
            tx_hash=HexBytes(claim_tx_hash),
            block_number=block_number,
            block_timestamp=block_timestamp,
            share_count=share_count,
            denomination_amount=denomination_amount,
        )

    # --- Generic async vault interface overrides ---

    def serialize_deposit_ticket(self, ticket: OstiumDepositTicket) -> dict:
        """Serialise an Ostium deposit ticket, including ``settlement_id``."""
        data = super().serialize_deposit_ticket(ticket)
        data["vault_settlement_id"] = ticket.settlement_id
        return data

    def reconstruct_deposit_ticket(self, data: dict) -> OstiumDepositTicket:
        """Reconstruct an :py:class:`OstiumDepositTicket` from serialised dict."""
        ts = data.get("vault_request_block_timestamp")
        return OstiumDepositTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            # int() accepts both the current string form and legacy int form
            raw_amount=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            gas_used=data.get("vault_request_gas_used", 0),
            block_number=data.get("vault_request_block_number", 0),
            block_timestamp=datetime.datetime.fromisoformat(ts) if ts else None,
            settlement_id=data["vault_settlement_id"],
        )

    def serialize_redemption_ticket(self, ticket: OstiumRedemptionTicket) -> dict:
        """Serialise an Ostium redemption ticket, including ``settlement_id``."""
        data = super().serialize_redemption_ticket(ticket)
        data["vault_settlement_id"] = ticket.settlement_id
        return data

    def reconstruct_redemption_ticket(self, data: dict) -> OstiumRedemptionTicket:
        """Reconstruct an :py:class:`OstiumRedemptionTicket` from serialised dict."""
        return OstiumRedemptionTicket(
            vault_address=data["vault_address"],
            owner=data["vault_owner"],
            to=data.get("vault_to", data["vault_owner"]),
            # int() accepts both the current string form and legacy int form
            raw_shares=int(data["vault_raw_amount"]),
            tx_hash=HexBytes(data["vault_request_tx_hash"]),
            settlement_id=data["vault_settlement_id"],
        )

    def get_deposit_request_status(self, ticket: OstiumDepositTicket) -> "AsyncVaultRequestStatus":
        """Query Ostium V1.5 deposit status and map to generic enum.

        Maps Ostium's internal status to :py:class:`AsyncVaultRequestStatus`
        without raising :py:class:`OstiumSettlementFailed`.
        """
        from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

        assert isinstance(ticket, OstiumDepositTicket)
        raw_status = self.vault.vault_contract.functions.getDepositStatus(ticket.owner, ticket.settlement_id).call()
        return self._map_ostium_status(raw_status)

    def get_redemption_request_status(self, ticket: OstiumRedemptionTicket) -> "AsyncVaultRequestStatus":
        """Query Ostium V1.5 withdrawal status and map to generic enum."""
        from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

        assert isinstance(ticket, OstiumRedemptionTicket)
        raw_status = self.vault.vault_contract.functions.getWithdrawStatus(ticket.owner, ticket.settlement_id).call()
        return self._map_ostium_status(raw_status)

    @staticmethod
    def _map_ostium_status(raw_status: int) -> "AsyncVaultRequestStatus":
        """Map Ostium's raw uint8 status to generic :py:class:`AsyncVaultRequestStatus`."""
        from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

        if raw_status == OSTIUM_REQUEST_STATUS_NONE:
            return AsyncVaultRequestStatus.none
        elif raw_status == OSTIUM_REQUEST_STATUS_PENDING:
            return AsyncVaultRequestStatus.pending
        elif raw_status == OSTIUM_REQUEST_STATUS_CLAIMABLE:
            return AsyncVaultRequestStatus.claimable
        elif raw_status == OSTIUM_REQUEST_STATUS_RECLAIMABLE:
            return AsyncVaultRequestStatus.reclaimable
        return AsyncVaultRequestStatus.none

    # --- Ticket-based status helpers (Ostium-specific) ---

    def get_deposit_ticket_status(self, ticket: OstiumDepositTicket) -> int:
        """Query deposit status for a specific ticket's settlement ID.

        :return:
            One of ``OSTIUM_REQUEST_STATUS_*`` constants:
            NONE(0), PENDING(1), CLAIMABLE(2), RECLAIMABLE(3)
        """
        return self.vault.vault_contract.functions.getDepositStatus(ticket.owner, ticket.settlement_id).call()

    def get_redemption_ticket_status(self, ticket: OstiumRedemptionTicket) -> int:
        """Query withdrawal status for a specific ticket's settlement ID.

        :return:
            One of ``OSTIUM_REQUEST_STATUS_*`` constants:
            NONE(0), PENDING(1), CLAIMABLE(2), RECLAIMABLE(3)
        """
        return self.vault.vault_contract.functions.getWithdrawStatus(ticket.owner, ticket.settlement_id).call()

    # --- Reclaim/cancel convenience methods ---

    def reclaim_deposit(self, ticket: OstiumDepositTicket) -> ContractFunction | None:
        """Return ``reclaimDeposit(settlementId)`` to recover funds after a failed settlement."""
        return self.vault.vault_contract.functions.reclaimDeposit(ticket.settlement_id)

    def reclaim_withdrawal(self, ticket: OstiumRedemptionTicket) -> ContractFunction | None:
        """Return ``reclaimWithdraw(settlementId)`` to recover shares after a failed settlement."""
        return self.vault.vault_contract.functions.reclaimWithdraw(ticket.settlement_id)

    def cancel_deposit(self, ticket: OstiumDepositTicket, raw_assets: int) -> ContractFunction:
        """Return ``cancelRequestDeposit(settlementId, assets)`` to cancel a pending deposit."""
        return self.vault.vault_contract.functions.cancelRequestDeposit(ticket.settlement_id, raw_assets)

    def cancel_withdrawal(self, ticket: OstiumRedemptionTicket, raw_shares: int) -> ContractFunction:
        """Return ``cancelRequestWithdraw(settlementId, shares)`` to cancel a pending withdrawal."""
        return self.vault.vault_contract.functions.cancelRequestWithdraw(ticket.settlement_id, raw_shares)

    def fetch_settlement_requests(
        self,
        owner: str,
    ) -> list[dict]:
        """Query on-chain status for all recent settlement IDs for an address.

        Checks ``getDepositStatus`` and ``getWithdrawStatus`` for settlement
        IDs in the range ``[lastSettlementId - 10, max(depositTarget, withdrawTarget)]``.
        This is fast (a few RPC calls) and avoids slow event scanning.

        :param owner:
            Address to check.

        :return:
            List of dicts with keys: ``settlement_id``, ``direction``
            (``"deposit"`` or ``"withdraw"``), ``status`` (raw int).
            Only includes entries with non-NONE status.
        """
        contract = self.vault.vault_contract

        last_id = contract.functions.lastSettlementId().call()
        deposit_target = contract.functions.targetSettlementId(True).call()
        withdraw_target = contract.functions.targetSettlementId(False).call()

        scan_start = max(1, last_id - 10)
        scan_end = max(deposit_target, withdraw_target) + 1

        results = []
        for sid in range(scan_start, scan_end):
            dep_status = contract.functions.getDepositStatus(owner, sid).call()
            if dep_status != OSTIUM_REQUEST_STATUS_NONE:
                results.append(
                    {
                        "settlement_id": sid,
                        "direction": "deposit",
                        "status": dep_status,
                    }
                )

            wd_status = contract.functions.getWithdrawStatus(owner, sid).call()
            if wd_status != OSTIUM_REQUEST_STATUS_NONE:
                results.append(
                    {
                        "settlement_id": sid,
                        "direction": "withdraw",
                        "status": wd_status,
                    }
                )

        return results
