"""Liquid Royalty vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class LiquidRoyaltyVault(ERC4626Vault):
    """Liquid Royalty vault.

    Liquid Royalty (ALAR SailOut Royalty) is a vault product on Berachain
    that uses USDe as the underlying asset. Part of the Liquidity Royalty
    protocol family with tiered vault architecture and profit spillover mechanisms.

    - Website: https://www.liquidroyalty.com/vaults
    - Smart contracts: https://github.com/stratosphere-network/LiquidRoyaltyContracts
    - Example vault: https://berascan.com/address/0x09cea16a2563c2d7d807c86f5b8da760389b5915
    """

    def has_custom_fees(self) -> bool:
        """Liquid Royalty vaults have a 20% early withdrawal penalty within 7-day cooldown."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee.

        :return:
            None as there is no explicit management fee.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee.

        :return:
            None as there is no explicit performance fee.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period.

        All Liquid Royalty vaults have a 7-day cooldown period.
        Early withdrawal within this period incurs a 20% liquidation penalty.

        :return:
            7 days cooldown period.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link."""
        return "https://www.liquidroyalty.com/vaults"
