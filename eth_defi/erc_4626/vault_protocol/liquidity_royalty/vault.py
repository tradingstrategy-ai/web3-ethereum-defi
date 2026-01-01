"""Liquidity Royalty Tranching vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class LiquidityRoyalyJuniorVault(ERC4626Vault):
    """Liquidity Royalty Tranching junior vault.

    Liquidity Royalty Tranching is a protocol implementing a two-tranche vault system
    with profit spillover and cascading backstop mechanisms on Berachain.

    - Website: https://github.com/stratosphere-network/LiquidRoyaltyContracts
    - Documentation: https://github.com/stratosphere-network/LiquidRoyaltyContracts/tree/master/docs
    - Example vault: https://berascan.com/address/0x3a0A97DcA5e6CaCC258490d5ece453412f8E1883
    """

    @property
    def name(self) -> str:
        """Override the vault name.

        The on-chain name may not be descriptive enough.
        """
        return "Liquidity Royalty Tranching: Junior"

    def has_custom_fees(self) -> bool:
        """Liquidity Royalty vaults have withdrawal penalties."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee.

        Liquidity Royalty does not have explicit management fees.

        :return:
            None as there is no management fee.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee.

        Liquidity Royalty does not have explicit performance fees,
        but there is a 20% early withdrawal penalty if cooldown is not met,
        plus a 1% base withdrawal fee.

        :return:
            None as there is no performance fee, only withdrawal penalties.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period.

        The protocol has a 7-day cooldown period to avoid the 20% early
        withdrawal penalty.

        :return:
            7 days cooldown period.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        Currently there is no web UI, so we link to the GitHub repository.
        """
        return "https://github.com/stratosphere-network/LiquidRoyaltyContracts"
