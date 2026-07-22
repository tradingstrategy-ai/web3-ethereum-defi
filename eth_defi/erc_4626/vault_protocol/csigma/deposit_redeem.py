"""cSigma ERC-4626 deposit and redemption requests."""

from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable


class CsigmaDepositManager(ERC4626DepositManager):
    """Synchronous cSigma ERC-4626 deposit and redemption flow.

    **Supported simulation path**

    Standard ``deposit`` and ``redeem`` calls against the cSigma V2 pool. The
    manager preflights the native share capacity returned by ``maxRedeem`` and
    :meth:`force_settle` accepts ``None`` for the shared synchronous no-op.

    **Known limitations**

    This manager does not process cSigma FIFO queues, reserve replenishment,
    partial redemptions or repeated redemption claims. A capacity result is a
    point-in-time advisory and can change before transaction inclusion.
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
            available_raw_shares = self.fetch_redeemable_raw_shares(owner)
            if raw_shares > available_raw_shares:
                reason = "cSigma redemption exceeds immediate share capacity"
                raise VaultFlowUnavailable(
                    reason,
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="request",
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
