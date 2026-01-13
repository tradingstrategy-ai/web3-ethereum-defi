"""Brink vault support.

Brink is a DeFi protocol providing yield-bearing vaults on Mantle and other chains.

- Homepage: https://brink.money/
- App: https://brink.money/app
- Documentation: https://doc.brink.money/
- Twitter: https://x.com/BrinkDotMoney
- Example vault: https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254

BrinkVault uses modified events instead of standard ERC-4626:
- DepositFunds(uint256 assetBalance) instead of Deposit
- WithdrawFunds(uint256 assetBalance) instead of Withdraw

Fees are internalised in the share price. No explicit fee getter functions are exposed on-chain.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class BrinkVault(ERC4626Vault):
    """Brink vault.

    Brink vaults are ERC4626-compliant yield-bearing vaults that use modified events
    (DepositFunds/WithdrawFunds) instead of standard ERC-4626 Deposit/Withdraw events.

    - Homepage: https://brink.money/
    - App: https://brink.money/app
    - Documentation: https://doc.brink.money/
    - Twitter: https://x.com/BrinkDotMoney
    - Example vault: https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254
    """

    def has_custom_fees(self) -> bool:
        """Brink vaults do not expose explicit fee getters on-chain."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get management fee.

        Fees are internalised in the share price. No explicit fee getter available on-chain.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get performance fee.

        Fees are internalised in the share price. No explicit fee getter available on-chain.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period.

        Brink vaults support instant redemption.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault page.

        Returns a link to the Brink app.
        """
        return "https://brink.money/app"
