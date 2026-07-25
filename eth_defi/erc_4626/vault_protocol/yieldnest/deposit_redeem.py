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

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

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
    (``baseWithdrawalFee()``). Note that
    :meth:`YieldNestVault.get_deposit_manager_capability` still advertises
    ``can_redeem=False`` (``maturity_aware_redemption_flow_not_implemented``) to
    trade-executor: the buffer redemption implemented here is not treated as a
    fully fork-proven redemption lifecycle for the maturity-bearing vaults.

    **Queues and settlement**

    None modelled. The manager only exposes the immediate buffer redemption; any
    queue-based withdrawal beyond the buffer is out of scope and is surfaced as
    the typed capacity refusal above rather than as an onchain ticket.

    **Lockups and cooldowns**

    No generic cooldown. The one exception is the ``ynRWAx`` vault, which has a
    fixed maturity date of 15 October 2026:
    :meth:`YieldNestVault.get_estimated_lock_up` returns the remaining time until
    that date before maturity and ``None`` afterwards (and ``None`` for all other
    YieldNest vaults).

    **Whitelisting / access control**

    Permissionless. No per-account whitelist or access manager.

    **Anvil settlement (force_settle)**

    No-op. Both directions are synchronous, so :meth:`force_settle` accepts
    ``None`` for the shared synchronous no-op; there is no ticket to settle.
    """

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
