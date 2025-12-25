"""Yearn vault support."""

import datetime
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class YearnV3Vault(ERC4626Vault):
    """Yearn V3 vaults.

    - Yearn v3 vaults are ERC-4626 compliant vaults with multiple strategies, built wit Vyper (not Solidity)
    - Yearn vault can have multiple strategies, identified by calling `get_default_queue()`.
    - Withdraw happens in the order of this strategy queue
    - The queue strategies can be `SiloStrategy` and other Yearn vault contracts
    - Fees are internatilised and are built into the share price: The strategies takes profit by minting more shares to the strategies themselves.
      This is why external fees are set to zero.

    More information:

    - Example `Yearn v3 vault <https://arbiscan.io/address/0x9fa306b1f4a6a83fec98d8ebbabedff78c407f6b>`__ (Vyper)
    - `Vault contract on Github <https://github.com/yearn/yearn-vaults-v3/blob/master/contracts/VaultV3.vy>`__
    - Example `SiloStrategy contract <https://arbiscan.io/address/0xA4B8873B4629c20f2167c0A2bC33B6AF8699dDc1#code>`__
    - `Yearn's own internal vault metadata JSON endpoint <https://ydaemon.yearn.fi/vaults/detected?limit=2000>`__ - check for `isRetired` flag
    - Use `Yearn Powerglove to explore exposure and allocation of Yearn vaults <https://yearn-powerglove.vercel.app/vaults/42161/0xb739AE19620f7ECB4fb84727f205453aa5bc1AD2>`__

    Max withdrawl:

    .. code-block:: vyper

        def _max_withdraw(
            owner: address,
            max_loss: uint256,
            strategies: DynArray[address, MAX_QUEUE]
        ) -> uint256:
            '''
            @dev Returns the max amount of `asset` an `owner` can withdraw.

            This will do a full simulation of the withdraw in order to determine
            how much is currently liquid and if the `max_loss` would allow for the
            tx to not revert.

            This will track any expected loss to check if the tx will revert, but
            not account for it in the amount returned since it is unrealised and
            therefore will not be accounted for in the conversion rates.

            i.e. If we have 100 debt and 10 of unrealised loss, the max we can get
            out is 90, but a user of the vault will need to call withdraw with 100
            in order to get the full 90 out.
            '''

    Withdrawal:

    .. code-block:: vyper

        @internal
        def _withdraw_from_strategy(strategy: address, assets_to_withdraw: uint256):
            '''
            This takes the amount denominated in asset and performs a {redeem}
            with the corresponding amount of shares.

            We use {redeem} to natively take on losses without additional non-4626 standard parameters.
            '''
            # Need to get shares since we use redeem to be able to take on losses.
            shares_to_redeem: uint256 = min(
                # Use previewWithdraw since it should round up.
                IStrategy(strategy).previewWithdraw(assets_to_withdraw),
                # And check against our actual balance.
                IStrategy(strategy).balanceOf(self)
            )
            # Redeem the shares.
            IStrategy(strategy).redeem(shares_to_redeem, self, self)

    Taking profit:

    .. code-block:: vyper

        def _process_report(strategy: address) -> (uint256, uint256):
            ```
            Processing a report means comparing the debt that the strategy has taken
            with the current amount of funds it is reporting. If the strategy owes
            less than it currently has, it means it has had a profit, else (assets < debt)
            it has had a loss.

            Different strategies might choose different reporting strategies: pessimistic,
            only realised P&L, ... The best way to report depends on the strategy.

            The profit will be distributed following a smooth curve over the vaults
            profit_max_unlock_time seconds. Losses will be taken immediately, first from the
            profit buffer (avoiding an impact in pps), then will reduce pps.

            Any applicable fees are charged and distributed during the report as well
            to the specified recipients.

            Can update the vaults `totalIdle` to account for any airdropped tokens by
            passing the vaults address in as the parameter.
            ```
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""

        # TODO: Detect SiloStrategy, use different ABI if needed

        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="yearn/YearnV3Vault.json",
        )

    def get_default_queue(self) -> list[HexAddress]:
        return self.vault_contract.functions.get_default_queue().call()

    def fetch_strategies(self) -> list[Contract]:
        return self.vault_contract.functions.getStrategies().call()

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(0)

    def get_link(self, referral: str | None = None) -> str:
        return f"https://yearn.fi/v3/{self.chain_id}/{self.vault_address}"
