"""Caller-aware deposit and redemption flow for IPOR Fusion vaults."""

# ruff: noqa: FBT001, FBT002, PLR0917

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, extract_revert_data

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault

#: ``FailedInnerCall()`` emitted by OpenZeppelin's ``Address`` utility when
#: PlasmaVault cannot withdraw enough underlying from one of its configured
#: markets. ``keccak("FailedInnerCall()")[:4]``.
IPOR_FAILED_INNER_CALL_SELECTOR = HexBytes("0x1425ea42")

#: ``WithdrawManagerInvalidSharesToRelease(uint256,uint256,uint256)`` emitted
#: when PlasmaVault's withdrawal manager cannot release the requested shares.
#: ``keccak("WithdrawManagerInvalidSharesToRelease(uint256,uint256,uint256)")[:4]``.
IPOR_WITHDRAW_MANAGER_INVALID_SHARES_TO_RELEASE_SELECTOR = HexBytes("0x3c71a1e7")

#: ``AccountIsLocked(uint256)`` emitted by PlasmaVault while the caller's
#: redemption lock remains active. ``keccak("AccountIsLocked(uint256)")[:4]``.
IPOR_ACCOUNT_IS_LOCKED_SELECTOR = HexBytes("0xa592703b")

IPOR_REDEMPTION_ERROR_NAMES = {
    IPOR_FAILED_INNER_CALL_SELECTOR: "FailedInnerCall",
    IPOR_WITHDRAW_MANAGER_INVALID_SHARES_TO_RELEASE_SELECTOR: "WithdrawManagerInvalidSharesToRelease",
    IPOR_ACCOUNT_IS_LOCKED_SELECTOR: "AccountIsLocked",
}

ERROR_SELECTOR_LENGTH = 4


class IPORDepositManager(ERC4626DepositManager):
    """IPOR Fusion manager with OpenZeppelin AccessManager pre-flights.

    IPOR Fusion vaults are standard synchronous ERC-4626 vaults guarded by an
    OpenZeppelin :solidity:`AccessManager`. Once a caller is admitted, both
    deposit and redemption are ordinary ERC-4626 transactions; the only
    protocol-specific behaviour this manager adds is an admission preflight that
    converts a predictable access rejection or scheduling requirement into a
    typed :class:`VaultFlowUnavailable` before any approval or transaction is
    broadcast. Deployments without a readable AccessManager fall back to the
    generic ERC-4626 manager (see :meth:`IPORVault.get_deposit_manager`) and do
    not use this class.

    **Deposit process**

    Synchronous ERC-4626 after access admission. :meth:`create_deposit_request`
    first calls :meth:`_assert_immediate_access` for the deposit selector
    (``deposit(uint256,address)``), then delegates to the base manager for the
    shared ``approve`` + ``deposit`` calls, ``maxDeposit`` capacity check and
    balance check. IPOR is utilisation-based, so deposits close when
    ``maxDeposit`` reads zero; an optional atomist-configured deposit fee is
    already reflected in ``previewDeposit``.

    **Redemption process**

    Synchronous ERC-4626 after access admission. :meth:`create_redemption_request`
    calls :meth:`_assert_immediate_access` for the redemption selector
    (``redeem(uint256,address,address)``) — resolved independently, because the
    access policy can differ from the deposit selector — then delegates to the
    base manager. Every PlasmaVault deployment also simulates its exact
    ``redeem()`` path and refuses a full-fill request that its market fuses or
    withdrawal manager cannot satisfy immediately.

    **Queues and settlement**

    None (synchronous). There is no request queue, ticket or operator
    settlement; an admitted ``redeem`` completes in one transaction.

    **Lockups and cooldowns**

    Access-manager driven. IPOR's AccessManager exposes
    ``REDEMPTION_DELAY_IN_SECONDS`` (surfaced by
    :meth:`IPORVault.get_redemption_delay` and
    :meth:`IPORVault.get_estimated_lock_up`) and a per-account
    ``getAccountLockTime`` (:meth:`IPORVault.get_redemption_delay_over`). On many
    IPOR vaults this delay is zero, but when configured it manifests as a
    non-zero ``access_delay`` in the redemption preflight below.

    **Whitelisting / access control**

    OpenZeppelin AccessManager, checked per caller and selector.
    :meth:`_assert_immediate_access` reads ``canCall(caller, vault, selector)``
    returning ``(immediate, delay)``: ``immediate`` admits the transaction; a
    ``delay > 0`` means the call must be scheduled and is refused as
    :class:`VaultFlowUnavailable` (``"IPOR access requires delayed execution"``,
    carrying ``access_delay``); a ``(False, 0)`` result — an unauthorised caller,
    a closed target or an IPOR-specific temporary redemption lock — is refused as
    ``"IPOR AccessManager does not allow immediate vault flow"``. Both refusals
    carry the guarded ``function_selector``. A deployment without a readable
    AccessManager raises :class:`NotImplementedError` from the preflight (and, at
    vault construction, is routed to the generic manager instead).
    :meth:`IPORVault.is_whitelisted_deposit` reports whether the deposit selector
    is restricted away from ``PUBLIC_ROLE``.

    **Anvil settlement (force_settle)**

    No-op. Both directions are synchronous, so :meth:`force_settle` accepts
    ``None`` for the shared synchronous no-op; there is no ticket to settle.
    """

    def __init__(self, vault: "IPORVault") -> None:
        """Bind the manager to an IPOR vault.

        :param vault:
            IPOR Fusion vault exposing its AccessManager address.
        """
        super().__init__(vault)

    @property
    def vault(self) -> "IPORVault":
        """Return the manager's IPOR vault with a precise type."""
        return self._vault

    @vault.setter
    def vault(self, vault: "IPORVault") -> None:
        """Store the vault accepted by the common manager constructor."""
        self._vault = vault

    def _assert_immediate_access(
        self,
        owner: HexAddress,
        selector: HexBytes,
        direction: Literal["deposit", "redeem"],
    ) -> None:
        """Reject a caller that cannot immediately use an IPOR selector.

        :param owner:
            Account that will submit the transaction.
        :param selector:
            Four-byte ERC-4626 selector guarded by AccessManager.
        :param direction:
            Diagnostic flow direction.

        :raise VaultFlowUnavailable:
            If the selector is denied or requires scheduled execution.
        :raise NotImplementedError:
            If the deployment has no readable AccessManager.
        """
        immediate, delay = self.vault.fetch_selector_access(owner, selector)
        if immediate:
            return

        if delay > 0:
            reason = "IPOR access requires delayed execution"
            decoded_error = None
        else:
            # ``canCall() == (False, 0)`` can mean an unauthorised caller, a
            # closed target, or an IPOR-specific temporary redemption lock.
            # It does not decode a particular revert error by itself.
            reason = "IPOR AccessManager does not allow immediate vault flow"
            decoded_error = None

        raise VaultFlowUnavailable(
            reason,
            protocol="IPOR Fusion",
            vault_address=self.vault.address,
            caller=owner,
            direction=direction,
            phase="preflight",
            decoded_error=decoded_error,
            function_selector=selector,
            access_delay=delay,
        )

    def fetch_redeem_simulation(self, owner: HexAddress, raw_shares: int) -> tuple[bool, HexBytes | None]:
        """Simulate an exact PlasmaVault redemption without broadcasting it.

        A PlasmaVault only exposes the caller's share balance through
        ``maxRedeem()``, not the liquidity that its market fuses and withdrawal
        manager can release. The exact ``redeem()`` simulation is the
        authoritative immediate-execution check. Transport and ABI failures are
        deliberately propagated so callers can classify them as infrastructure
        failures rather than a vault availability result.

        :param owner:
            Share owner, receiver, and simulated transaction sender.
        :param raw_shares:
            Native share amount to redeem.
        :return:
            ``(True, None)`` when the call succeeds, otherwise ``(False,
            revert_data)`` when the node confirms an EVM revert.
        :raise ValueError:
            If the provider fails without EVM revert data.
        """
        try:
            self.vault.vault_contract.functions.redeem(raw_shares, owner, owner).call({"from": owner})
        except (ContractLogicError, ValueError) as error:
            revert_data = extract_revert_data(error)
            if isinstance(error, ValueError) and revert_data is None:
                raise
            return False, revert_data
        return True, None

    def fetch_redeemable_raw_shares(self, owner: HexAddress) -> int:
        """Find the largest immediate full-fill redemption for PlasmaVault shares.

        The verified PlasmaVault implementation repeatedly invokes
        ``_withdrawFromMarkets()`` before its ERC-4626 transfer. A redemption
        is monotonic in requested shares for this deployment, so binary search
        over the owner balance yields an immediate-execution cap without
        mutating onchain state. :meth:`create_redemption_request` repeats an
        exact simulation before refusing a requested amount, because these
        independent RPC calls do not form an atomic state snapshot. RPC and ABI
        failures propagate so the caller can classify infrastructure failures
        correctly.

        :param owner:
            Address whose PlasmaVault shares are being preflighted.
        :return:
            Maximum raw shares that can be redeemed in full immediately.
        """
        owner_raw_shares = int(self.vault.vault_contract.functions.balanceOf(owner).call())

        if owner_raw_shares == 0:
            return 0
        succeeds, full_revert_data = self.fetch_redeem_simulation(owner, owner_raw_shares)
        if succeeds:
            return owner_raw_shares

        full_error_selector = full_revert_data[:ERROR_SELECTOR_LENGTH] if full_revert_data and len(full_revert_data) >= ERROR_SELECTOR_LENGTH else None
        if full_error_selector == IPOR_ACCOUNT_IS_LOCKED_SELECTOR:
            return 0

        lower_bound = 0
        upper_bound = owner_raw_shares
        while upper_bound - lower_bound > 1:
            candidate = (lower_bound + upper_bound) // 2
            succeeds, _revert_data = self.fetch_redeem_simulation(owner, candidate)
            if succeeds:
                lower_bound = candidate
            else:
                upper_bound = candidate
        return lower_bound

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC4626DepositRequest:
        """Create a standard ERC-4626 deposit after access admission.

        :param owner:
            Account submitting and signing the deposit.
        :param to:
            Optional receiver, limited by the shared ERC-4626 manager.
        :param amount:
            Human-readable denomination amount.
        :param raw_amount:
            Raw denomination amount.
        :param check_max_deposit:
            Whether to run the shared ERC-4626 capacity check.
        :param check_enough_token:
            Whether to run the shared balance check.
        :return:
            Preflighted deposit request.
        """
        self._assert_immediate_access(owner, self.vault.get_deposit_function_selector(), "deposit")
        return super().create_deposit_request(
            owner=owner,
            to=to,
            amount=amount,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
        check_max_redeem: bool = True,
    ) -> ERC4626RedemptionRequest:
        """Create a standard ERC-4626 redemption after access and liquidity preflight.

        :param owner:
            Account submitting and signing the redemption.
        :param to:
            Optional receiver, limited by the shared ERC-4626 manager.
        :param shares:
            Human-readable share amount.
        :param raw_shares:
            Raw share amount.
        :param check_max_deposit:
            Compatibility argument forwarded to the shared manager.
        :param check_enough_token:
            Whether to run the shared share-balance check.
        :param check_max_redeem:
            Whether to check immediately redeemable capacity.
        :return:
            Preflighted redemption request.
        """
        assert not to, f"Unsupported to={to}"
        self._assert_immediate_access(owner, self.vault.get_redeem_function_selector(), "redeem")
        if raw_shares is None:
            assert shares is not None, "Either raw_shares or shares must be supplied"
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        if check_max_redeem:
            available_raw_shares = self.fetch_redeemable_raw_shares(owner)
            if raw_shares > available_raw_shares:
                succeeds, revert_data = self.fetch_redeem_simulation(owner, raw_shares)
                if not succeeds:
                    error_selector = revert_data[:ERROR_SELECTOR_LENGTH] if revert_data and len(revert_data) >= ERROR_SELECTOR_LENGTH else None
                    decoded_error = IPOR_REDEMPTION_ERROR_NAMES.get(error_selector)
                    if decoded_error == "AccountIsLocked":
                        reason = "IPOR PlasmaVault redemption is temporarily locked for this account"
                        preflight_result = "redemption_window_closed"
                    elif decoded_error in {"FailedInnerCall", "WithdrawManagerInvalidSharesToRelease"}:
                        reason = "IPOR PlasmaVault cannot source immediate redemption liquidity from configured markets"
                        preflight_result = "redemption_capacity_limited"
                    else:
                        reason = "IPOR PlasmaVault redemption is not immediately available"
                        preflight_result = "redemption_unavailable"
                    raise VaultFlowUnavailable(
                        reason,
                        protocol="IPOR Fusion",
                        vault_address=self.vault.address,
                        caller=owner,
                        direction="redeem",
                        phase="preflight",
                        decoded_error=decoded_error,
                        preflight_result=preflight_result,
                        raw_revert_data=revert_data,
                        error_selector=error_selector,
                        requested_raw_amount=raw_shares,
                        available_raw_amount=available_raw_shares,
                    )

        return super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
            check_max_redeem=False,
        )

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether an account can immediately call IPOR deposit.

        :param owner:
            Account to evaluate.
        :return:
            ``True`` only for immediate selector access.
        """
        try:
            self._assert_immediate_access(owner, self.vault.get_deposit_function_selector(), "deposit")
        except (VaultFlowUnavailable, NotImplementedError):
            return False
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return whether an account can immediately call IPOR redemption.

        :param owner:
            Account to evaluate.
        :return:
            ``True`` only for immediate selector access.
        """
        try:
            self._assert_immediate_access(owner, self.vault.get_redeem_function_selector(), "redeem")
        except (VaultFlowUnavailable, NotImplementedError):
            return False
        return self.fetch_redeemable_raw_shares(owner) > 0
