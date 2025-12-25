"""Goat vault support."""

import datetime
from decimal import Decimal
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault


logger = logging.getLogger(__name__)


class GoatVault(ERC4626Vault):
    """Goat protocol vaults.

    - An `example vault <https://arbiscan.io/address/0x8a1ef3066553275829d1c0f64ee8d5871d5ce9d3#code>`__
    - Each goat can have multiple strategies with each strategy with different unlocked and locked profit and loss
    - `Multistrategy contract on Github <https://github.com/goatfi/contracts/blob/main/src/infra/multistrategy/Multistrategy.sol>`__
    - Fees are internalised into the share price of the strategy (similar as Yearn, Harvest).

    Withdraw logic:

    .. code-block:: solidity

        /// @notice Handles withdrawals from the contract.
        ///
        /// This function performs the following actions:
        /// - If the caller is not the owner, it checks and spends the allowance for the withdrawal.
        /// - Ensures that the amount to be withdrawn is greater than zero.
        /// - If the requested withdrawal amount exceeds the available balance, it withdraws the necessary amount from the strategies in the withdrawal order.
        ///   - Iterates through the withdrawal queue, withdrawing from each strategy until the balance requirement is met or the queue is exhausted.
        ///   - Updates the total debt of both the strategy and the contract as assets are withdrawn.
        ///   - Requests the strategy to report, accounting for potential gains or losses.
        /// - Reverts if the withdrawal process does not result in sufficient balance.
        /// - Burns the corresponding shares and transfers the requested assets to the receiver.
        /// - Emits a `Withdraw` event with the caller, receiver, owner, amount of assets withdrawn, and shares burned.
        ///
        /// @param _caller The address of the entity initiating the withdrawal.
        /// @param _receiver The address of the recipient to receive the withdrawn assets.
        /// @param _owner The address of the owner of the shares being withdrawn.
        /// @param _assets The amount of assets to withdraw.
        /// @param _shares The amount of shares to burn.
        /// @param _consumeAllShares True if all `_shares` should be used to withdraw. False if it should withdraw just `_assets`.
        /// @return The number of assets withdrawn and the shares burned as a result of the withdrawal.
        function _withdraw(
            address _caller,
            address _receiver,
            address _owner,
            uint256 _assets,
            uint256 _shares,
            bool _consumeAllShares
        ) internal returns (uint256, uint256) {
            require(_shares > 0, Errors.ZeroAmount(_shares));

            if (_caller != _owner) {
                _spendAllowance(_owner, _caller, _shares);
            }

            uint256 assets = _consumeAllShares ? _convertToAssets(_shares, Math.Rounding.Floor) : _assets;

            if(assets > _balance()) {
                for(uint8 i = 0; i <= withdrawOrder.length; ++i){
                    address strategy = withdrawOrder[i];

                    // We reached the end of the withdraw queue and assets are still higher than the balance
                    require(strategy != address(0), Errors.InsufficientBalance(assets, _balance()));

                    // We can't withdraw from a strategy more than what it has asked as credit.
                    uint256 assetsToWithdraw = Math.min(assets - _balance(), strategies[strategy].totalDebt);
                    if(assetsToWithdraw == 0) continue;

                    uint256 withdrawn = IStrategyAdapter(strategy).withdraw(assetsToWithdraw);
                    strategies[strategy].totalDebt -= withdrawn;
                    totalDebt -= withdrawn;

                    IStrategyAdapter(strategy).askReport();

                    // Update assets, as a loss could have been reported and user should get less assets for
                    // the same amount of shares.
                    if(_consumeAllShares) assets = _convertToAssets(_shares, Math.Rounding.Floor);
                    if(assets <= _balance()) break;
                }
            }

            uint256 shares = _consumeAllShares ? _shares : _convertToShares(assets, Math.Rounding.Ceil);
            _burn(_owner, shares);
            IERC20(asset()).safeTransfer(_receiver, assets);

            emit Withdraw(_caller, _receiver, _owner, assets, shares);

            return (assets, shares);
        }

    Fee calculation:

        .. code-block:: solidity

        /// @notice Reports the performance of a strategy.
        ///
        /// This function performs the following actions:
        /// - Validates that the reporting strategy does not claim both a gain and a loss simultaneously.
        /// - Checks that the strategy has sufficient tokens to cover the debt repayment and the gain.
        /// - If there is a loss, it realizes the loss.
        /// - Calculates and deducts the performance fee from the gain.
        /// - Determines the excess debt of the strategy.
        /// - Adjusts the strategy's and contract's total debt accordingly.
        /// - Calculates and updates the new locked profit after accounting for any losses.
        /// - Updates the reporting timestamps for the strategy and the contract.
        /// - Transfers the debt repayment and the gains to this contract.
        ///
        /// Emits a `StrategyReported` event.
        ///
        /// @param _debtRepayment The amount of debt being repaid by the strategy.
        /// @param _gain The amount of profit reported by the strategy.
        /// @param _loss The amount of loss reported by the strategy.
        function _report(uint256 _debtRepayment, uint256 _gain, uint256 _loss) internal {
            uint256 strategyBalance = IERC20(asset()).balanceOf(msg.sender);
            require(!(_gain > 0 && _loss > 0), Errors.GainLossMismatch());
            require(strategyBalance >= _debtRepayment + _gain, Errors.InsufficientBalance(strategyBalance, _debtRepayment + _gain));

            uint256 profit = 0;
            uint256 feesCollected = 0;
            if(_loss > 0) _reportLoss(msg.sender, _loss);
            if(_gain > 0) {
                strategies[msg.sender].totalGain += _gain;
                feesCollected = _gain.mulDiv(performanceFee, MAX_BPS);
                profit = _gain - feesCollected;
            }

            uint256 debtToRepay = Math.min(_debtRepayment, _debtExcess(msg.sender));
            if(debtToRepay > 0) {
                strategies[msg.sender].totalDebt -= debtToRepay;
                totalDebt -= debtToRepay;
            }

            uint256 newLockedProfit = _calculateLockedProfit() + profit;
            if(newLockedProfit > _loss) {
                lockedProfit = newLockedProfit - _loss;
            } else {
                lockedProfit = 0;
            }

            strategies[msg.sender].lastReport = block.timestamp;
            lastReport = block.timestamp;

            if(debtToRepay + _gain > 0) IERC20(asset()).safeTransferFrom(msg.sender, address(this), debtToRepay + _gain);
            if(feesCollected > 0) IERC20(asset()).safeTransfer(protocolFeeRecipient, feesCollected);

            emit StrategyReported(msg.sender, debtToRepay, profit, _loss);
        }
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="goat/Multistrategy.json",
        )

    def fetch_pnl(self) -> tuple[Decimal, Decimal]:
        """Fetch profit and loss from the vault.

        .. code-block:: none

             * This function performs the following actions:
             * - Iterates through the `withdrawOrder` array, which defines the order in which strategies are withdrawn from.
             * - For each strategy in the `withdrawOrder`:
             *   - If the strategy address is zero, it breaks the loop, indicating the end of the list.
             *   - If the strategy has no debt, it skips to the next strategy.
             *   - Otherwise, it retrieves the current profit and loss (PnL) from the strategy by calling `currentPnL`.
             *   - Adds the strategy's profit to the total profit, after deducting the performance fee.
             *   - Adds the strategy's loss to the total loss.
             * - Returns the total profit and total loss across all active strategies.
             *
             * @return totalProfit The total profit across all active strategies, after deducting the performance fee.
             * @return totalLoss The total loss across all active strategies.

        :return: (locked, unlocked) PnL amounts
        """
        profit_raw, loss_raw = self.vault_contract.functions.currentPnL().call()
        profit = self.denomination_token.convert_to_decimals(profit_raw)
        loss = self.denomination_token.convert_to_decimals(loss_raw)
        return profit, loss

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Internalised to the share price"""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Internalised to the share price"""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(days=0)
