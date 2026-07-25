"""cSigma ERC-4626 deposit and redemption requests.

cSigma redemption model (important — do not mistake it for ERC-7540 async):

- The cSigma pool (``CsigmaV2Pool``) is a **reserve-limited synchronous**
  ERC-4626 vault. A ``redeem`` succeeds immediately for up to the reserve-backed
  capacity reported by ``maxRedeem(owner)``. Beyond that capacity — or when a
  withdrawal is already queued for the owner — ``redeem`` reverts the pool's
  custom ``WithdrawalPending()`` error (`0xb34f5c6c`), and the excess is queued
  off-chain for the ``withdrawalManager`` to service later.
- The pool exposes **no onchain request/ticket/claim surface** for that queue
  (no ``requestRedeem`` / ``pendingRedeemRequest`` / ``claimableRedeemRequest``
  / request id). The queue is entirely off-chain, so the queued portion cannot
  be modelled as a claimable async ticket the way Lagoon/Ember/Plutus can.
  Therefore this manager stays *synchronous* and instead **preflights the
  immediate capacity** and surfaces the queued/over-capacity case as a typed
  :class:`VaultFlowUnavailable` (decoded ``WithdrawalPending``) before broadcast
  rather than letting the raw revert escape.
"""

from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, VaultRedemptionPreflight

#: ``WithdrawalPending()`` custom-error selector reverted by the cSigma pool
#: when a redemption exceeds the immediate reserve-backed capacity or a
#: withdrawal is already queued for the owner. ``keccak("WithdrawalPending()")[:4]``.
CSIGMA_WITHDRAWAL_PENDING_SELECTOR = HexBytes("0xb34f5c6c")


class CsigmaDepositManager(ERC4626DepositManager):
    """Synchronous cSigma ERC-4626 deposit and reserve-limited redemption flow.

    **Supported simulation path**

    Standard ``deposit`` and ``redeem`` calls against the cSigma pool. The
    manager preflights the native share capacity returned by ``maxRedeem`` and
    :meth:`force_settle` accepts ``None`` for the shared synchronous no-op.

    **Known limitations**

    cSigma redemptions are reserve-limited: only up to ``maxRedeem(owner)`` is
    immediately redeemable, and the excess is queued off-chain by the
    ``withdrawalManager`` (the onchain ``redeem`` reverts ``WithdrawalPending``
    for it). This manager does **not** model that off-chain queue as a claimable
    ticket — the pool exposes no request/claim getters — so it preflights the
    immediate capacity and raises a typed ``VaultFlowUnavailable`` (decoded
    ``WithdrawalPending``) for the queued/over-capacity case. A capacity result
    is a point-in-time advisory and can change before transaction inclusion.
    """

    def fetch_redeemable_raw_shares(self, owner: HexAddress) -> int:
        """Fetch the cSigma redemption capacity expressed in raw shares.

        :param owner:
            Address whose immediate capacity is queried.
        :return:
            Maximum raw vault shares redeemable immediately by ``owner``.
        """
        return int(self.vault.vault_contract.functions.maxRedeem(owner).call())

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Fetch the cSigma deposit capacity expressed in raw assets.

        :param owner:
            Address that will receive cSigma vault shares.
        :return:
            Maximum raw denomination-token amount immediately depositable by
            ``owner``.
        """
        return int(self.vault.vault_contract.functions.maxDeposit(owner).call())

    def fetch_redemption_preflight(
        self,
        owner: HexAddress,
        raw_shares: int,
    ) -> VaultRedemptionPreflight:
        """Check cSigma's owner-specific immediate redemption capacity.

        ``maxRedeem(owner)`` is denominated in raw vault shares, so this
        compares the requested and available values without a
        rounding-sensitive share-to-asset conversion.

        .. note::

            Trade-executor must map an unavailable result, or the matching
            :class:`VaultFlowUnavailable` from
            :meth:`create_redemption_request`, to
            ``redemption_capacity_limited`` before treating generic request
            failures as receipt-analysis failures.

        :param owner:
            Address whose immediate capacity is queried.
        :param raw_shares:
            Requested raw cSigma vault shares.
        :return:
            Available capacity result in raw shares.
        """
        available_raw_shares = self.fetch_redeemable_raw_shares(owner)
        if raw_shares <= available_raw_shares:
            return VaultRedemptionPreflight(
                available=True,
                requested_raw_shares=raw_shares,
                available_raw_shares=available_raw_shares,
            )
        return VaultRedemptionPreflight(
            available=False,
            requested_raw_shares=raw_shares,
            available_raw_shares=available_raw_shares,
            reason="redemption_capacity_limited",
        )

    def create_deposit_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626DepositRequest:
        """Create a deposit request after checking cSigma asset capacity.

        :param owner:
            Address depositing denomination tokens and receiving shares.
        :param to:
            Retained for the base manager API compatibility.
        :param amount:
            Human-readable denomination-token amount when ``raw_amount`` is
            omitted.
        :param raw_amount:
            Requested raw denomination-token amount.
        :param check_max_deposit:
            Retained for the base manager API compatibility.
        :param check_enough_token:
            Retained for the base manager API compatibility.
        :return:
            Transaction request ready for broadcast.
        :raises VaultFlowUnavailable:
            If the requested assets exceed the current cSigma capacity.
        """
        if raw_amount is None:
            assert amount is not None, "Either raw_amount or amount must be supplied"
            raw_amount = self.vault.denomination_token.convert_to_raw(amount)

        if check_max_deposit:
            available_raw_assets = self.fetch_depositable_raw_assets(owner)
            if raw_amount > available_raw_assets:
                reason = "cSigma deposit exceeds immediate asset capacity"
                raise VaultFlowUnavailable(
                    reason,
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="deposit",
                    phase="request",
                    requested_raw_amount=raw_amount,
                    available_raw_amount=available_raw_assets,
                )

        return super().create_deposit_request(
            owner=owner,
            to=to,
            amount=amount,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )

    def create_redemption_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626RedemptionRequest:
        """Create a redemption request after checking cSigma share capacity.

        :param owner:
            Address owning the vault shares and receiving denomination tokens.
        :param to:
            Unsupported alternative receiver.
        :param shares:
            Human-readable share amount when ``raw_shares`` is omitted.
        :param raw_shares:
            Requested raw vault shares.
        :param check_max_deposit:
            Retained for the base manager API compatibility.
        :param check_enough_token:
            Retained for the base manager API compatibility.
        :return:
            Transaction request ready for broadcast.
        :raises VaultFlowUnavailable:
            If the requested shares exceed the current cSigma capacity.
        """
        if raw_shares is None:
            assert shares is not None, "Either raw_shares or shares must be supplied"
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        if check_max_deposit:
            preflight = self.fetch_redemption_preflight(owner, raw_shares)
            if not preflight.available:
                # Exceeding the immediate reserve-backed capacity is exactly what
                # makes the onchain redeem revert WithdrawalPending() and queue
                # the excess off-chain, so we tag the typed refusal with that
                # decoded error/selector to keep the mapping unambiguous.
                reason = "cSigma redemption exceeds immediate share capacity"
                raise VaultFlowUnavailable(
                    reason,
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="request",
                    requested_raw_amount=raw_shares,
                    available_raw_amount=preflight.available_raw_shares,
                    decoded_error="WithdrawalPending",
                    error_selector=CSIGMA_WITHDRAWAL_PENDING_SELECTOR,
                )

        return super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Report whether cSigma presently offers immediate redemption capacity.

        :param owner:
            Address whose capacity is queried.
        :return:
            ``True`` when at least one raw share is currently redeemable.
        """
        return self.fetch_redeemable_raw_shares(owner) > 0

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Report whether cSigma presently offers immediate deposit capacity.

        :param owner:
            Address whose capacity is queried.
        :return:
            ``True`` when at least one raw asset is currently depositable.
        """
        return self.fetch_depositable_raw_assets(owner) > 0
