"""Synchronous Veranta execution with preflight for post-withdrawal limits."""

from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.erc_4626.estimate import estimate_4626_redeem
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, extract_revert_data


class VerantaDepositManager(ERC4626DepositManager):
    """Keep standard ERC-4626 calls and simulate exact redemptions.

    Veranta checks utilisation after transferring both payout and fee. Its
    ``maxRedeem`` estimate cannot prove that the final check will pass.
    Simulation catches predictable reverts without changing custody, guard
    policy or requested share amounts.
    """

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Preserve a real zero deposit capacity.

        The older implementation inherits an unlimited ``maxDeposit`` despite
        enforcing ``depositCap`` in its deposit path. Take the tighter of the
        public maximum and the actual strict cap in both reviewed versions.

        :param owner: Account receiving shares.
        :return: Maximum raw USDC deposit, including zero.
        """
        functions = self.vault.vault_contract.functions
        maximum = int(functions.maxDeposit(owner).call())
        cap = int(functions.depositCap().call())
        assets = int(functions.totalAssets().call())
        return min(maximum, max(0, cap - assets - 1))

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
        """Build and simulate the exact owner-to-owner redemption.

        A genuine zero capacity is unavailable, unlike the generic manager's
        compatibility convention. Transport errors propagate; EVM reverts are
        reported as typed temporary unavailability with their revert data.

        :param owner: Custody address owning and receiving the redemption.
        :param to: Separate receivers are unsupported by the shared manager.
        :param shares: Human-readable amount, alternative to raw shares.
        :param raw_shares: Exact native share amount to redeem.
        :param check_max_deposit: Shared interface compatibility argument.
        :param check_enough_token: Check the owner's share balance.
        :param check_max_redeem: Check current advertised capacity.
        :return: Standard synchronous redemption request after simulation.
        :raise VaultFlowUnavailable: If capacity is zero or the exact call reverts.
        """
        if check_max_redeem and self.vault.vault_contract.functions.maxRedeem(owner).call() == 0:
            raise VaultFlowUnavailable(
                "Veranta owner has no redeemable shares at current capacity",
                protocol="Veranta",
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                requested_raw_amount=raw_shares if raw_shares is not None else self.vault.share_token.convert_to_raw(shares) if shares is not None else None,
                available_raw_amount=0,
            )
        request = super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
            check_max_redeem=check_max_redeem,
        )
        try:
            payout = request.funcs[0].call({"from": owner})
        except (ContractLogicError, ValueError) as error:
            revert_data = extract_revert_data(error)
            if isinstance(error, ValueError) and revert_data is None:
                raise
            raise VaultFlowUnavailable(
                "Veranta exact redemption reverted; liquidity or withdrawal limits prevent this exit",
                protocol="Veranta",
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                preflight_result="redemption_call_reverted",
                requested_raw_amount=request.raw_shares,
                raw_revert_data=revert_data,
                error_selector=revert_data[:4] if revert_data and len(revert_data) >= 4 else None,
            ) from error
        if payout <= 0:
            raise VaultFlowUnavailable(
                "Veranta redemption would return no assets",
                protocol="Veranta",
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                preflight_result="zero_payout",
                requested_raw_amount=request.raw_shares,
            )
        return request

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Use the fee-adjusted preview without substituting a gross share price.

        A failed or zero preview must not turn into an optimistic fee-free quote.
        The protocol preview retains its fee-inclusive integer rounding.

        :param owner: Custody account receiving the redemption.
        :param shares: Human-readable shares to quote.
        :param block_identifier: State at which to obtain the exact preview.
        :return: Fee-adjusted human-readable USDC amount.
        """
        return estimate_4626_redeem(self.vault, owner, shares, block_identifier=block_identifier, fallback_using_share_price=False)

    def create_redemption_request_for_analysis(self, owner: HexAddress, raw_shares: int) -> ERC4626RedemptionRequest:
        """Rebuild a mined redemption parser without repeating live preflights.

        The original shares have already been burned, so balance, capacity and
        simulation checks cannot describe the transaction being analysed.

        :param owner: Original custody account.
        :param raw_shares: Exact native share quantity from the original request.
        :return: Standard request shape for parsing the mined receipt.
        """
        return super().create_redemption_request(owner=owner, shares=self.vault.share_token.convert_to_decimals(raw_shares), raw_shares=raw_shares, check_enough_token=False, check_max_redeem=False)
