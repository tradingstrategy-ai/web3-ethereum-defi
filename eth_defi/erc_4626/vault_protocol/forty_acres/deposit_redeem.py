"""Immediate-liquidity preflight for 40acres ERC-4626 redemptions."""

# ruff: noqa: FBT001, FBT002, PLR0917

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_typing import HexAddress
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3RPCError

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault

logger = logging.getLogger(__name__)

#: Legacy ERC-20 revert emitted when the vault's direct USDC transfer cannot
#: cover a synchronous redemption. The exact Pharaoh deployment does not use
#: OpenZeppelin 5's ``ERC20InsufficientBalance`` custom error.
FORTY_ACRES_INSUFFICIENT_LIQUIDITY_ERROR = "ERC20: transfer amount exceeds balance"


class FortyAcresDepositManager(ERC4626DepositManager):
    """40acres ERC-4626 manager with a direct-underlying redemption preflight.

    40acres includes deployed loans in ``totalAssets()``, but its verified
    ERC-4626 ``redeem()`` implementation pays lenders by directly transferring
    the underlying from the vault. It cannot pull liquidity from its loan
    contract during a redemption. This manager therefore limits an immediate
    redemption to the current underlying balance held by the vault and refuses
    a larger request before constructing ``redeem()``.

    Deposits remain the inherited synchronous ERC-4626 flow. Redemptions remain
    synchronous too; the manager does not model a loan repayment queue because
    the protocol exposes no public redemption request or claim lifecycle.
    """

    def __init__(self, vault: "FortyAcresVault") -> None:
        """Bind the manager to a 40acres vault.

        :param vault:
            40acres ERC-4626 vault whose direct underlying balance authorises
            immediate redemptions.
        """
        super().__init__(vault)

    @property
    def vault(self) -> "FortyAcresVault":
        """Return the bound 40acres vault with its concrete type.

        :return:
            Bound 40acres vault adapter.
        """
        return self._vault

    @vault.setter
    def vault(self, vault: "FortyAcresVault") -> None:
        """Store the vault accepted by the shared manager constructor.

        :param vault:
            40acres vault to retain for request creation.
        """
        self._vault = vault

    def fetch_redeemable_raw_shares(self, owner: HexAddress) -> int:
        """Read the owner-bounded share amount the vault can redeem immediately.

        The verified 40acres implementation is standard OpenZeppelin ERC-4626:
        its ``_withdraw()`` transfers the requested assets directly from the
        vault. ``totalAssets()`` also includes lent capital and is therefore not
        a payout authority. A failed balance or conversion read is fail-closed
        and reports zero capacity.

        :param owner:
            Address whose shares would be redeemed.
        :return:
            Maximum raw shares that can be redeemed in full immediately.
        """
        try:
            owner_raw_shares = int(self.vault.vault_contract.functions.balanceOf(owner).call())
            idle_raw_assets = int(self.vault.denomination_token.fetch_raw_balance_of(self.vault.address))
            idle_raw_shares = int(self.vault.vault_contract.functions.convertToShares(idle_raw_assets).call())
        except (BadFunctionCallOutput, ContractLogicError, Web3RPCError, ValueError) as error:
            logger.warning(
                "Cannot read 40acres immediate redemption capacity for vault %s on chain %d: %s",
                self.vault.address,
                self.vault.chain_id,
                error,
            )
            return 0

        return min(owner_raw_shares, idle_raw_shares)

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
        """Preflight 40acres liquidity before constructing a redemption call.

        :param owner:
            Share owner and transaction caller.
        :param to:
            Unsupported alternative receiver retained for the shared API.
        :param shares:
            Human-readable share amount when ``raw_shares`` is absent.
        :param raw_shares:
            Native share amount to redeem.
        :param check_max_deposit:
            Retained for shared manager API compatibility.
        :param check_enough_token:
            Whether the inherited manager checks the owner share balance.
        :param check_max_redeem:
            Whether to enforce the protocol-specific immediate liquidity cap.
        :return:
            Synchronous ERC-4626 redemption request when capacity is sufficient.
        :raises VaultFlowUnavailable:
            If the requested redemption exceeds direct underlying liquidity.
        """
        if raw_shares is None:
            assert shares is not None, "Either raw_shares or shares must be supplied"
            raw_shares = self.vault.share_token.convert_to_raw(shares)

        if check_max_redeem:
            available_raw_shares = self.fetch_redeemable_raw_shares(owner)
            if raw_shares > available_raw_shares:
                reason = "40acres vault lacks immediate underlying liquidity for redemption"
                raise VaultFlowUnavailable(
                    reason,
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="redeem",
                    phase="preflight",
                    decoded_error=FORTY_ACRES_INSUFFICIENT_LIQUIDITY_ERROR,
                    preflight_result="redemption_capacity_limited",
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
            # OpenZeppelin's generic maxRedeem() returns owner shares and does
            # not describe 40acres' direct-underlying payout capacity.
            check_max_redeem=False,
        )

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Report whether 40acres has any immediate redemption liquidity.

        :param owner:
            Address whose current redemption capacity is checked.
        :return:
            ``True`` when at least one raw share can complete immediately.
        """
        return self.fetch_redeemable_raw_shares(owner) > 0
