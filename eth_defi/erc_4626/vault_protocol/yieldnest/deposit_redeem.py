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

    Deposits use the inherited synchronous ERC-4626 flow. Redemptions add a
    capacity preflight so an over-buffer redemption is refused as a typed
    ``VaultFlowUnavailable`` (decoded ``ExceededMaxRedeem``) rather than a raw
    onchain revert.
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
