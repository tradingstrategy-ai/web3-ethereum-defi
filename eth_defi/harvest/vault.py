"""Harvest Finance vault support."""

import datetime
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class HarvestVault(ERC4626Vault):
    """Harvest vaults.

    - VaultV1 has underlying strategy contract
    - Each vault has only one strategy
    - Uses a custom proxy pattern not supported by Etherscan family explorers
    - Strategy itself is a proxy
    - Strategy contract inherits from SiloVaultStrategy which contains the main fee logic

    - https://github.com/harvestfi/harvest-strategy-arbitrum/blob/1e53688004af1b31e64fd569f04bf19ec7d4bc16/contracts/base/VaultV1.sol#L18


    Harvest Finance SileVaultStrategy vaults account fees in their profit collection, thus all fees are always reflected in the share price and fees for Harvest Finance are set to zero.
    This may or may not be correct.

    .. code-block:: solidity

            function _liquidateRewards() internal {
            if (!sell()) {
              // Profits can be disabled for possible simplified and rapid exit
              emit ProfitsNotCollected(sell(), false);
              return;
            }
            _handleFee();
            }


          function _handleFee() internal {
            _accrueFee();
            uint256 fee = pendingFee();
            if (fee > 100) {
              _redeem(fee);
              address _underlying = underlying();
              fee = Math.min(fee, IERC20(_underlying).balanceOf(address(this)));
              uint256 balanceIncrease = fee.mul(feeDenominator()).div(totalFeeNumerator());
              _notifyProfitInRewardToken(_underlying, balanceIncrease);
              setUint256(_PENDING_FEE_SLOT, pendingFee().sub(fee));
            }
          }

    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment.

        - Example impl https://arbiscan.io/address/0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75#code
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="harvest/VaultV2.json",
        )

    @property
    def name(self) -> str:
        """Fix broken Harvest vault names."""
        name = super().name
        if name == "FARM_USDC":
            return f"Harvest: USDC Vault ({self.vault_address[0:6]})"
        return name

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def fetch_strategy(self) -> Contract:
        """Fetch the strategy contract used by this vault.

        Example strategy:
        - Impl https://arbiscan.io/address/0x573a918F255E751054f7751975D0577Cb82F947b#code
        - Proxy https://arbiscan.io/address/0x5f19640De4A50e035AB0b50957748bFdEd8F158D#code
        """
        addr = self.vault_contract.functions.strategy().call()
        return get_deployed_erc_4626_contract(
            self.web3,
            addr,
            abi_fname="harvest/SiloVaultStrategy.json",
        )

    def fetch_pending_fee(self) -> int:
        """How many tokens of fee the strategy may collect on next profit taking."""
        return self.strategy.functions.pendingFee().call()

    def fetch_ftoken(self) -> TokenDetails:
        ftoken_addr = self.strategy.functions.fToken().call()
        return fetch_erc20_details(self.web3, ftoken_addr)

    @cached_property
    def strategy(self) -> Contract:
        return self.fetch_strategy()

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(0)
