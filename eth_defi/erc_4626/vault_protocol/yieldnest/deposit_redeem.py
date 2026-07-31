"""YieldNest deposit and redemption manager.

YieldNest deposits are standard synchronous ERC-4626 deposits. Redemptions are
buffer-limited: the vault only permits redeeming up to ``maxRedeem(owner)`` and
otherwise reverts ``ExceededMaxRedeem(address,uint256,uint256)`` (`0xb8b8b59c`).
This manager preflights that capacity and raises a typed
:class:`~eth_defi.vault.deposit_redeem.VaultFlowUnavailable` before broadcast,
carrying the decoded error and the requested-vs-available raw share amounts,
instead of letting the raw revert selector escape.
"""

from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ABIFunctionNotFound

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YNRWAX_MATURITY_DATE, YNRWAX_VAULT_ADDRESS
from eth_defi.provider.anvil import is_anvil
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.deposit_redeem import DepositTicket, RedemptionTicket, UnsupportedVaultSimulation, VaultFlowUnavailable, VaultForcedSettlementResult

#: ``ExceededMaxRedeem(address owner, uint256 shares, uint256 maxShares)``
#: custom-error selector reverted by the YieldNest vault when a redemption
#: exceeds the buffer-limited ``maxRedeem(owner)``. ``keccak(...)[:4]``.
EXCEEDED_MAX_REDEEM_SELECTOR = HexBytes("0xb8b8b59c")


class YieldNestDepositManager(ERC4626DepositManager):
    """YieldNest adapter: synchronous deposits, buffer-limited redemptions.

    YieldNest vaults are liquid-restaking ERC-4626 vaults. Deposits are ordinary
    synchronous ERC-4626 deposits; redemptions are served synchronously from the
    vault's liquid buffer up to ``maxRedeem(owner)`` and revert otherwise. This
    manager adds a redemption capacity preflight on top of the inherited flow.

    **Deposit process**

    Fully synchronous ERC-4626, inherited unchanged from
    :class:`~eth_defi.erc_4626.deposit_redeem.ERC4626DepositManager` (shared
    ``approve`` + ``deposit``). The vault does not expose a usable
    ``maxDeposit(address(0))`` probe (:meth:`YieldNestVault.can_check_deposit`
    returns ``False``), so there is no owner-independent capacity advisory; the
    concrete deposit still runs the inherited balance/allowance checks. No
    minimum deposit or per-account gate.

    **Redemption process**

    Buffer-limited *synchronous* ``redeem``. :meth:`create_redemption_request`
    reads ``maxRedeem(owner)`` and refuses an over-buffer request as
    :class:`VaultFlowUnavailable` carrying the decoded
    ``ExceededMaxRedeem(address,uint256,uint256)`` error and its selector
    ``0xb8b8b59c``, then delegates to the base manager with the duplicate
    ``maxRedeem`` read disabled. Withdrawal fees are dynamic
    (``baseWithdrawalFee()``). The manager advertises the implemented
    synchronous lifecycle independently from live availability. ynRWAx
    requests before 15 October 2026 return the typed
    ``redemption_not_yet_matured`` result; after maturity the immediate buffer
    preflight applies normally.

    **Queues and settlement**

    None modelled. The manager only reads the immediate buffer capacity; any
    queue-based withdrawal beyond it is out of scope and is surfaced as the
    typed capacity refusal above rather than as an onchain ticket.

    **Lockups and cooldowns**

    No generic cooldown. The one exception is the ``ynRWAx`` vault, which has a
    fixed maturity date of 15 October 2026:
    :meth:`YieldNestVault.get_estimated_lock_up` returns the remaining time until
    that date before maturity and ``None`` afterwards (and ``None`` for all other
    YieldNest vaults).

    **Whitelisting / access control**

    Permissionless. No per-account whitelist or access manager.

    **Anvil settlement (force_settle)**

    No real settlement is modelled and the public capability remains
    deposit-only. A synchronous deposit uses the inherited no-op
    :meth:`force_settle` behaviour. Focused local tests may explicitly call
    ``force_settle(None, mock=..., ignore_liquidity=True)`` on a
    ``MockYieldNestVault``. It switches only that mock's ``maxRedeem`` gate on,
    allowing the standard guarded redemption call to be tested. It never makes
    a real YieldNest fork redeemable.
    """

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Report whether the owner has non-zero immediate redemption capacity.

        This is deliberately narrower than the inherited unconditional ERC-4626
        answer. It reflects only ``maxRedeem(owner)`` and does not infer a
        queue-based or maturity settlement route.

        :param owner:
            Share owner whose current immediate buffer capacity is queried.
        :return:
            ``True`` only when ``maxRedeem(owner)`` is non-zero.
        """
        return self.vault.vault_contract.functions.maxRedeem(owner).call() > 0

    def force_settle(
        self,
        ticket: DepositTicket | RedemptionTicket | None,
        *,
        mock: object | None = None,
        ignore_liquidity: bool = False,
    ) -> VaultForcedSettlementResult:
        """Enable the local YieldNest liquidity override for a focused mock test.

        YieldNest's immediate redemption is synchronous, so a real deployment
        has no settlement transaction to force. The deliberately explicit
        local-mock path is a test fixture: it changes only
        ``MockYieldNestVault``'s ``maxRedeem`` admission gate before a guarded
        ``redeem`` is built. It cannot add live liquidity or bypass the
        production vault's ``ExceededMaxRedeem`` check.

        :param ticket:
            Must be ``None`` because YieldNest has no asynchronous ticket.
        :param mock:
            The deployed ``MockYieldNestVault`` at this manager's vault
            address.
        :param ignore_liquidity:
            Enable the mock-only liquidity override. Defaults to ``False`` and
            retains the normal no-op/rejection behaviour.
        :return:
            A result recording the direct mock configuration transaction.
        :raise UnsupportedVaultSimulation:
            If this is not Anvil, the mock is absent or unrelated, a ticket was
            supplied, or the mock does not expose the dedicated test hook.
        """
        if not ignore_liquidity:
            return super().force_settle(ticket, mock=mock, ignore_liquidity=False)

        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                "YieldNest liquidity override requires an Anvil provider",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        if ticket is not None:
            raise UnsupportedVaultSimulation(
                "YieldNest liquidity override requires no asynchronous ticket",
                unsupported_reason="yieldnest_synchronous_ticket_must_be_none",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        mock_address = getattr(mock, "address", None)
        if mock_address is None or Web3.to_checksum_address(mock_address) != Web3.to_checksum_address(self.vault.address):
            raise UnsupportedVaultSimulation(
                "YieldNest liquidity override requires the manager's exact MockYieldNestVault",
                unsupported_reason="mock_settlement_vault_mismatch",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        try:
            tx_hash = mock.functions.setIgnoreLiquidity(True).transact({"from": self.web3.eth.accounts[0]})
        except (ABIFunctionNotFound, AttributeError) as e:
            raise UnsupportedVaultSimulation(
                "YieldNest liquidity override mock does not expose setIgnoreLiquidity(bool)",
                unsupported_reason="yieldnest_liquidity_override_mock_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            ) from e

        return VaultForcedSettlementResult(
            ticket=None,
            settlement_required=True,
            status_before=None,
            status_after=None,
            transaction_hashes=(HexBytes(tx_hash),),
            liquidity_constraints_ignored=True,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
        check_max_redeem=True,
    ) -> ERC4626RedemptionRequest:
        """Preflight the buffer-limited redemption capacity, then build the request.

        :param owner:
            Share owner and controller.
        :param to:
            Unsupported alternative receiver.
        :param shares:
            Human-readable share amount when ``raw_shares`` is omitted.
        :param raw_shares:
            Requested raw vault shares.
        :param check_max_deposit:
            Retained for base manager API compatibility.
        :param check_enough_token:
            Preflight the owner's current share balance.
        :param check_max_redeem:
            When set, refuse a redemption that exceeds ``maxRedeem(owner)``.
        :return:
            Synchronous ERC-4626 redemption request.
        :raise VaultFlowUnavailable:
            When the requested shares exceed the vault's current
            ``maxRedeem(owner)`` buffer capacity (decoded ``ExceededMaxRedeem``,
            selector ``0xb8b8b59c``).
        """
        if raw_shares is None and shares is not None:
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        if self.vault.address.lower() == YNRWAX_VAULT_ADDRESS:
            block_timestamp = get_block_timestamp(self.web3, self.web3.eth.block_number)
            if block_timestamp < YNRWAX_MATURITY_DATE:
                raise VaultFlowUnavailable(
                    f"YieldNest ynRWAx does not mature until {YNRWAX_MATURITY_DATE.isoformat()}",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    decoded_error="RedemptionBeforeMaturity",
                    preflight_result="redemption_not_yet_matured",
                    requested_raw_amount=raw_shares,
                    next_open=YNRWAX_MATURITY_DATE,
                )

        if check_max_redeem and raw_shares is not None:
            # maxRedeem(owner) is queryable per-owner (only the address(0)
            # probe is unsupported), so it can gate the concrete request.
            max_redeem = self.vault.vault_contract.functions.maxRedeem(owner).call()
            if raw_shares > max_redeem:
                raise VaultFlowUnavailable(
                    f"YieldNest redemption exceeds buffer capacity for vault {self.vault.address} on chain {self.vault.chain_id}: requested {raw_shares} shares, maxRedeem {max_redeem}",
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    decoded_error="ExceededMaxRedeem",
                    error_selector=EXCEEDED_MAX_REDEEM_SELECTOR,
                    requested_raw_amount=raw_shares,
                    available_raw_amount=max_redeem,
                )

        # The capacity check above already read maxRedeem(owner) and raised on
        # over-capacity, so disable the base manager's duplicate maxRedeem read.
        return super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
            check_max_redeem=False,
        )
