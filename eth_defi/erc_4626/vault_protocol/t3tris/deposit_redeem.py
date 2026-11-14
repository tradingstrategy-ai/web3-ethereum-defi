"""T3tris asynchronous deposit and redemption flow.

T3tris vaults in closed mode take deposits and redemptions as requests. The
vault curator settles all pending requests of the current epoch, and the
owner then claims the minted shares or the redeemed assets. The lifecycle
matches `ERC-7540 <https://eips.ethereum.org/EIPS/eip-7540>`__, but the
function signatures differ, so this manager reuses the generic queued
manager and overrides only the calls whose ABI differs.

See ``README-t3tris.md`` in this package for the verified call semantics and
the GuardV0 coverage.
"""

import logging
from collections.abc import Callable
from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.logs import DISCARD

from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.erc_7540.deposit_redeem import (
    ERC7540DepositManager,
    ERC7540DepositRequest,
    ERC7540DepositTicket,
    ERC7540RedemptionRequest,
    ERC7540RedemptionTicket,
)
from eth_defi.provider.anvil import is_anvil, make_anvil_custom_rpc_request, mine
from eth_defi.timestamp import get_block_timestamp
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    DepositRedeemEventAnalysis,
    DepositRedeemEventFailure,
    DepositTicket,
    RedemptionTicket,
    UnsupportedVaultSimulation,
    VaultDepositManager,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    VaultRequestInvariantViolation,
    WhitelistingRequired,
)

logger = logging.getLogger(__name__)

#: Minimal T3tris oracle interface used by the Anvil settlement driver.
#:
#: No oracle ABI is published. ``setTotalAssets(uint256)`` is the call the
#: oracle owner sends onchain before each settlement.
T3TRIS_ORACLE_ABI = [
    {"type": "function", "name": "owner", "inputs": [], "outputs": [{"name": "", "type": "address"}], "stateMutability": "view"},
    {"type": "function", "name": "setTotalAssets", "inputs": [{"name": "totalAssets", "type": "uint256"}], "outputs": [], "stateMutability": "nonpayable"},
]

#: ``feeClaimType`` passed by the T3tris curator in observed settlements.
T3TRIS_SETTLE_FEE_CLAIM_TYPE = 0

#: Stable preflight result for a vault in synchronous open mode.
T3TRIS_OPEN_MODE_UNSUPPORTED = "synchronous_mode_unsupported"

#: Stable preflight result when the owner already has an open request.
T3TRIS_REQUEST_IN_PROGRESS = "request_in_progress"


class T3trisDepositManager(ERC7540DepositManager):
    """Asynchronous T3tris request, settlement and claim flow.

    **Deposit process.** Asynchronous. The owner approves the denomination
    token to the vault, then :meth:`create_deposit_request` builds
    ``requestDeposit(owner, false, assets, "")``. The vault-wide epoch id is
    read from the ``DepositRequest`` event into an
    :class:`~eth_defi.erc_7540.deposit_redeem.ERC7540DepositTicket`. After
    the curator settles the epoch, :meth:`finish_deposit` builds
    ``claimDeposit(owner)``.

    **Redemption process.** Asynchronous. :meth:`create_redemption_request`
    builds ``requestRedeem(owner, owner, owner, false, shares)``; after
    settlement :meth:`finish_redemption` builds
    ``claimRedeem(owner, owner, false)``.

    **Queues and settlement.** Request ids are vault-wide epochs and the
    per-owner state is keyed by ``(owner, requestId)``. Claims are
    owner-wide, and a new request first claims any settled request in the
    same direction. This manager therefore refuses a new request while the
    owner has a pending or claimable one, and reads every ticket's status
    for its own request id.

    **Whitelisting / access control.** Deposits use the shared
    :meth:`~eth_defi.vault.deposit_redeem.VaultDepositManager.check_deposit_whitelist`
    preflight backed by ``isDepositWhitelisted``. Redemptions check
    ``isWithdrawWhitelisted`` when the withdrawal whitelist is enabled.

    **Unsupported surfaces.** Open-mode synchronous vaults, Permit2 deposits,
    ``unsafe`` flows and request decreases are not implemented.

    **Anvil settlement.** :meth:`force_settle` impersonates the oracle owner
    to refresh the valuation and the curator to call ``settle()``.
    """

    def __init__(self, vault: T3trisVault):
        """Initialise the manager for a T3tris vault.

        :param vault:
            T3tris vault adapter. It is not an ``ERC7540Vault`` because its
            request and claim ABI differs from the standard.
        """
        assert isinstance(vault, T3trisVault), f"Got {type(vault)}"
        VaultDepositManager.__init__(self, vault)

    def _unavailable(self, reason: str, owner: HexAddress, direction: str, preflight_result: str) -> VaultFlowUnavailable:
        """Build a typed preflight refusal.

        :param reason: Human-readable refusal reason.
        :param owner: Request owner.
        :param direction: ``deposit`` or ``redeem``.
        :param preflight_result: Stable consumer-facing result.
        :return: Exception to raise.
        """
        return VaultFlowUnavailable(
            f"{reason} for vault {self.vault.address} on chain {self.vault.chain_id}",
            protocol=self.vault.get_protocol_name(),
            vault_address=self.vault.address,
            caller=owner,
            direction=direction,
            phase="preflight",
            preflight_result=preflight_result,
        )

    def _fetch_status(self, owner: HexAddress, request_id: int, *, deposit: bool) -> AsyncVaultRequestStatus:
        """Read the status of one owner's request in one epoch.

        :param owner: Request owner.
        :param request_id: Vault-wide request epoch.
        :param deposit: ``True`` for a deposit, ``False`` for a redemption.
        :return: Claimable, pending or empty request status.
        """
        f = self.vault.vault_contract.functions
        settled = f.getSettledDepositShares(owner, request_id) if deposit else f.getSettledRedeemAssets(owner, request_id)
        if settled.call() > 0:
            return AsyncVaultRequestStatus.claimable
        requested = f.getRequestDepositAmount(owner, request_id) if deposit else f.getRequestRedeemAmount(owner, request_id)
        if requested.call() > 0:
            return AsyncVaultRequestStatus.pending
        return AsyncVaultRequestStatus.none

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        """Check whether the owner has a pending or claimable deposit request.

        :param owner: Request owner.
        :return: ``True`` when the owner's latest deposit request is open.
        """
        request_id = self.vault.vault_contract.functions.getLastDepositRequestId(owner).call()
        return self._fetch_status(owner, request_id, deposit=True) is not AsyncVaultRequestStatus.none

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        """Check whether the owner has a pending or claimable redemption request.

        :param owner: Request owner.
        :return: ``True`` when the owner's latest redemption request is open.
        """
        request_id = self.vault.vault_contract.functions.getLastRedeemRequestId(owner).call()
        return self._fetch_status(owner, request_id, deposit=False) is not AsyncVaultRequestStatus.none

    def _assert_deposit_request_available(self, owner: HexAddress) -> None:
        """Reject a deposit request that the vault would refuse.

        :param owner: Request owner and share receiver.
        :raise VaultFlowUnavailable:
            If the vault is paused, closed, in open mode, or the owner already
            has an open deposit request.
        :raise WhitelistingRequired:
            If the deposit whitelist excludes the owner.
        """
        f = self.vault.vault_contract.functions
        if self._is_vault_paused():
            raise self._unavailable("T3tris deposit requests are paused", owner, "deposit", "deposit_paused")
        if f.isVaultOpen().call():
            raise self._unavailable("T3tris open-mode synchronous deposits are not supported", owner, "deposit", T3TRIS_OPEN_MODE_UNSUPPORTED)
        if not f.isDepositEnabled().call() or f.isEndOfFund().call():
            raise self._unavailable("T3tris deposits are closed", owner, "deposit", "deposit_closed")
        self.check_deposit_whitelist(owner)
        if self.is_deposit_in_progress(owner):
            raise self._unavailable(f"Owner {owner} already has an open T3tris deposit request", owner, "deposit", T3TRIS_REQUEST_IN_PROGRESS)

    def _assert_redemption_request_available(self, owner: HexAddress) -> None:
        """Reject a redemption request that the vault would refuse.

        :param owner: Share owner and asset receiver.
        :raise VaultFlowUnavailable:
            If the vault is paused, in open mode or end of fund, or the owner
            already has an open redemption request.
        :raise WhitelistingRequired:
            If the withdrawal whitelist excludes the owner.
        """
        f = self.vault.vault_contract.functions
        if self._is_vault_paused():
            raise self._unavailable("T3tris redemption requests are paused", owner, "redeem", "redemption_paused")
        if f.isVaultOpen().call():
            raise self._unavailable("T3tris open-mode synchronous redemptions are not supported", owner, "redeem", T3TRIS_OPEN_MODE_UNSUPPORTED)
        if f.isEndOfFund().call():
            raise self._unavailable("T3tris end-of-fund redemptions are not supported", owner, "redeem", "redemption_unavailable")
        if f.isWithdrawWhitelistEnabled().call() and not f.isWithdrawWhitelisted(owner).call():
            raise WhitelistingRequired(
                f"Redeemer {owner} is not whitelisted for vault {self.vault.address} on chain {self.vault.chain_id}",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
            )
        if self.is_redemption_in_progress(owner):
            raise self._unavailable(f"Owner {owner} already has an open T3tris redemption request", owner, "redeem", T3TRIS_REQUEST_IN_PROGRESS)

    @staticmethod
    def _passes(preflight: Callable[[HexAddress], None], owner: HexAddress) -> bool:
        """Convert a raising preflight into a boolean.

        :param preflight: Preflight that raises :class:`VaultFlowUnavailable`.
        :param owner: Request owner.
        :return: ``True`` when the preflight does not refuse the request.
        """
        try:
            preflight(owner)
        except VaultFlowUnavailable:
            return False
        return True

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether a deposit request would pass the preflight.

        :param owner: Request owner.
        :return: ``True`` when no preflight refusal applies.
        """
        return self._passes(self._assert_deposit_request_available, owner)

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return whether a redemption request would pass the preflight.

        :param owner: Share owner.
        :return: ``True`` when no preflight refusal applies.
        """
        return self._passes(self._assert_redemption_request_available, owner)

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC7540DepositRequest:
        """Build a T3tris ``requestDeposit`` transaction.

        :param owner:
            Asset owner and share receiver.
        :param to:
            Must be ``None`` or ``owner``; T3tris claims to the request receiver.
        :param amount:
            Human-readable denomination-token amount.
        :param raw_amount:
            Raw denomination-token amount, overriding ``amount``.
        :param check_max_deposit:
            Ignored. ``maxDeposit`` describes only the open-mode flow.
        :param check_enough_token:
            Verify that the owner holds the denomination tokens.
        :return:
            Request wrapper ready for signing and parsing.
        :raise VaultFlowUnavailable:
            If a preflight refuses the request.
        """
        assert to is None or to == owner, f"Unsupported to={to}"
        self._assert_deposit_request_available(owner)
        token = self.vault.denomination_token
        if not raw_amount:
            raw_amount = token.convert_to_raw(amount)
        if check_enough_token and token.fetch_raw_balance_of(owner) < raw_amount:
            raise self._unavailable(f"Owner {owner} holds less than {raw_amount} raw {token.symbol}", owner, "deposit", "insufficient_assets")
        func = self.vault.vault_contract.functions.requestDeposit(owner, False, raw_amount, b"")
        return self.deposit_request_class(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            amount=token.convert_to_decimals(raw_amount),
            raw_amount=raw_amount,
        )

    def _build_redemption_request(self, owner: HexAddress, raw_shares: int) -> ERC7540RedemptionRequest:
        """Build the ``requestRedeem`` wrapper without preflight checks.

        :param owner: Share owner and asset receiver.
        :param raw_shares: Raw vault shares to redeem.
        :return: Request wrapper.
        """
        func = self.vault.vault_contract.functions.requestRedeem(owner, owner, owner, False, raw_shares)
        return self.redemption_request_class(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            shares=self.vault.share_token.convert_to_decimals(raw_shares),
            raw_shares=raw_shares,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC7540RedemptionRequest:
        """Build a T3tris ``requestRedeem`` transaction.

        :param owner:
            Share owner and asset receiver.
        :param to:
            Must be ``None`` or ``owner``.
        :param shares:
            Human-readable vault-share amount.
        :param raw_shares:
            Raw vault-share amount, overriding ``shares``.
        :param check_max_deposit:
            Ignored; kept for the common manager API.
        :param check_enough_token:
            Verify that the owner holds the requested shares.
        :return:
            Request wrapper ready for signing and parsing.
        :raise VaultFlowUnavailable:
            If a preflight refuses the request.
        """
        assert to is None or to == owner, f"Unsupported to={to}"
        self._assert_redemption_request_available(owner)
        if not raw_shares:
            raw_shares = self.vault.share_token.convert_to_raw(shares)
        if check_enough_token and self.vault.share_token.fetch_raw_balance_of(owner) < raw_shares:
            raise self._unavailable(f"Owner {owner} holds less than {raw_shares} raw shares", owner, "redeem", "insufficient_shares")
        return self._build_redemption_request(owner, raw_shares)

    def create_redemption_request_for_analysis(self, owner: HexAddress, raw_shares: int) -> ERC7540RedemptionRequest:
        """Rebuild a mined redemption request without rerunning preflights.

        The open-request preflight would reject the very request being
        analysed.

        :param owner: Original share owner.
        :param raw_shares: Requested raw shares.
        :return: Request parser with the original call shape.
        """
        return self._build_redemption_request(owner, raw_shares)

    def get_deposit_request_status(self, ticket: ERC7540DepositTicket) -> AsyncVaultRequestStatus:
        """Map the ticket's own request epoch to the generic status.

        :param ticket: Deposit ticket.
        :return: Claimable, pending or empty request status.
        """
        return self._fetch_status(ticket.owner, ticket.request_id, deposit=True)

    def get_redemption_request_status(self, ticket: ERC7540RedemptionTicket) -> AsyncVaultRequestStatus:
        """Map the ticket's own request epoch to the generic status.

        :param ticket: Redemption ticket.
        :return: Claimable, pending or empty request status.
        """
        return self._fetch_status(ticket.owner, ticket.request_id, deposit=False)

    def can_finish_deposit(self, deposit_ticket: ERC7540DepositTicket) -> bool:
        """Check whether the ticket's deposit epoch has been settled.

        :param deposit_ticket: Deposit ticket.
        :return: ``True`` when shares are claimable.
        """
        return self.get_deposit_request_status(deposit_ticket) is AsyncVaultRequestStatus.claimable

    def can_finish_redeem(self, redemption_ticket: ERC7540RedemptionTicket) -> bool:
        """Check whether the ticket's redemption epoch has been settled.

        :param redemption_ticket: Redemption ticket.
        :return: ``True`` when assets are claimable.
        """
        return self.get_redemption_request_status(redemption_ticket) is AsyncVaultRequestStatus.claimable

    def finish_deposit(self, deposit_ticket: DepositTicket) -> ContractFunction:
        """Build ``claimDeposit(receiver)`` for a settled request.

        :param deposit_ticket: Settled deposit ticket.
        :return: Bound claim function.
        """
        return self.vault.vault_contract.functions.claimDeposit(deposit_ticket.to)

    def finish_redemption(self, redemption_ticket: RedemptionTicket) -> ContractFunction:
        """Build ``claimRedeem(owner, receiver, false)`` for a settled request.

        :param redemption_ticket: Settled redemption ticket.
        :return: Bound claim function.
        """
        return self.vault.vault_contract.functions.claimRedeem(redemption_ticket.owner, redemption_ticket.to, False)

    def _analyse_claim(
        self,
        claim_tx_hash: HexBytes | str,
        ticket: ERC7540DepositTicket | ERC7540RedemptionTicket,
        *,
        deposit: bool,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Decode a ``ClaimDeposit`` or ``ClaimRedeem`` event for a ticket.

        Both events are ``(requestId indexed, owner indexed, receiver indexed,
        assets, shares)``.

        :param claim_tx_hash: Claim transaction hash.
        :param ticket: Request ticket being claimed.
        :param deposit: ``True`` for a deposit claim.
        :return: Token movement, or a failed-transaction diagnostic.
        :raise VaultRequestInvariantViolation:
            If the claim does not belong to the ticket.
        """
        assert ticket is not None, "A request ticket is needed to analyse a T3tris claim"
        web3 = self.web3
        tx_hash = HexBytes(claim_tx_hash)
        direction = "deposit" if deposit else "redeem"
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            return DepositRedeemEventFailure(
                tx_hash=tx_hash,
                revert_reason="Transaction reverted",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction=direction,
                phase="claim",
                receipt_status=int(receipt["status"]),
            )
        event = self.vault.vault_contract.events.ClaimDeposit() if deposit else self.vault.vault_contract.events.ClaimRedeem()
        logs = [log for log in event.process_receipt(receipt, errors=DISCARD) if log["address"].lower() == self.vault.address.lower()]
        if len(logs) != 1:
            raise RuntimeError(f"Expected exactly one T3tris {event.event_name} event at {tx_hash.hex()}, got {len(logs)}")
        args = logs[0]["args"]
        if args["requestId"] != ticket.request_id or args["owner"].lower() != ticket.owner.lower() or args["receiver"].lower() != ticket.to.lower():
            raise VaultRequestInvariantViolation(
                f"T3tris claim {tx_hash.hex()} is for request {args['requestId']} of {args['owner']}, not the ticket's request {ticket.request_id} of {ticket.owner}",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=ticket.owner,
                direction=direction,
                phase="claim",
                preflight_result="request_reconciliation_required",
            )
        block_number = receipt["blockNumber"]
        return DepositRedeemEventAnalysis(
            from_=Web3.to_checksum_address(args["owner"]),
            to=Web3.to_checksum_address(args["receiver"]),
            share_count=self.vault.share_token.convert_to_decimals(args["shares"]),
            denomination_amount=self.vault.denomination_token.convert_to_decimals(args["assets"]),
            tx_hash=tx_hash,
            block_number=block_number,
            block_timestamp=get_block_timestamp(web3, block_number),
        )

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a ``claimDeposit`` transaction.

        :param claim_tx_hash: Claim transaction hash.
        :param deposit_ticket: Original request ticket.
        :return: Shares received and assets deposited, or a failure.
        """
        return self._analyse_claim(claim_tx_hash, deposit_ticket, deposit=True)

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a ``claimRedeem`` transaction.

        :param claim_tx_hash: Claim transaction hash.
        :param redemption_ticket: Original request ticket.
        :return: Shares burned and assets received, or a failure.
        """
        return self._analyse_claim(claim_tx_hash, redemption_ticket, deposit=False)

    def _send_as(self, account: HexAddress, func: ContractFunction) -> HexBytes:
        """Send one transaction as an impersonated protocol actor on Anvil.

        :param account: Protocol actor to impersonate.
        :param func: Bound contract call.
        :return: Successful transaction hash.
        """
        web3 = self.web3
        make_anvil_custom_rpc_request(web3, "anvil_impersonateAccount", [account])
        try:
            make_anvil_custom_rpc_request(web3, "anvil_setBalance", [account, hex(10**18)])
            tx_hash = HexBytes(func.transact({"from": account, "gas": 2_000_000}))
            assert_transaction_success_with_explanation(web3, tx_hash)
            return tx_hash
        finally:
            make_anvil_custom_rpc_request(web3, "anvil_stopImpersonatingAccount", [account])

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Settle the current T3tris epoch on an Anvil fork.

        Refreshes the oracle valuation as the oracle owner, at unchanged total
        assets, then settles as the curator. Settlement processes every
        pending request in the epoch, not only the ticket's. A redemption
        settlement pulls assets from the curator wallet, so it fails as a
        typed result when the curator cannot pay.

        :param ticket:
            Pending deposit or redemption ticket.
        :param mock:
            Unsupported; T3tris has no local mock settlement driver.
        :param ignore_liquidity:
            Unsupported; curator liquidity is never synthesised.
        :return:
            Before/after status and the two settlement transaction hashes.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil, an unsupported option is given, the
            ticket is not pending or the settlement does not complete.
        """
        direction = "deposit" if isinstance(ticket, DepositTicket) else "redeem" if isinstance(ticket, RedemptionTicket) else None

        def unsupported(message: str, reason: str) -> UnsupportedVaultSimulation:
            return UnsupportedVaultSimulation(
                f"{message} for vault {self.vault.address} on chain {self.vault.chain_id}",
                unsupported_reason=reason,
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction=direction,
            )

        if not is_anvil(self.web3) or mock is not None or ignore_liquidity:
            raise unsupported("T3tris settlement requires unmodified Anvil state", "t3tris_anvil_state_required")
        if direction is None:
            raise unsupported("T3tris settlement needs a request ticket", "t3tris_ticket_required")

        status = self.get_deposit_request_status if direction == "deposit" else self.get_redemption_request_status
        before = status(ticket)
        if before is AsyncVaultRequestStatus.claimable:
            return VaultForcedSettlementResult(ticket, False, before, before)
        if before is not AsyncVaultRequestStatus.pending:
            raise unsupported("T3tris request is not pending", "t3tris_request_not_pending")

        f = self.vault.vault_contract.functions
        oracle = self.web3.eth.contract(address=f.getOracle().call(), abi=T3TRIS_ORACLE_ABI)
        oracle_owner = oracle.functions.owner().call()
        curator = f.getCurator().call()
        total_assets = f.totalAssets().call()

        # Each settlement needs a valuation newer than the previous settlement.
        mine(self.web3, increase_timestamp=1)
        logger.info("Refreshing T3tris oracle %s valuation at %d raw assets as owner %s", oracle.address, total_assets, oracle_owner)
        try:
            valuation_tx = self._send_as(oracle_owner, oracle.functions.setTotalAssets(total_assets))
            logger.info("Settling all pending T3tris requests in vault %s as curator %s", self.vault.address, curator)
            settle_tx = self._send_as(curator, f.settle(T3TRIS_SETTLE_FEE_CLAIM_TYPE, False, b""))
        except TransactionAssertionError as e:
            raise unsupported(f"T3tris settlement reverted: {e.revert_reason}", "t3tris_settlement_reverted") from e

        after = status(ticket)
        if after is not AsyncVaultRequestStatus.claimable:
            raise unsupported("T3tris settlement did not make the request claimable", "t3tris_settlement_incomplete")
        return VaultForcedSettlementResult(ticket, True, before, after, transaction_hashes=(valuation_tx, settle_tx))
