"""Foxify vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, WithdrawalPeriod

logger = logging.getLogger(__name__)


class FoxifyVault(ERC4626Vault):
    """Foxify vaults.

    Foxify is a decentralised perpetual futures trading platform on Sonic blockchain.
    The vault allows users to deposit USDC and earn yield from trading fees.

    - Website: https://www.foxify.trade/
    - Documentation: https://docs.foxify.trade/
    - Twitter: https://x.com/foxifytrade
    - Example vault: https://sonicscan.org/address/0x3ccff8c929b497c1ff96592b8ff592b45963e732
    """

    @property
    def name(self) -> str:
        # Originak is "LP TOKEN"
        return "Foxify LP Vault"

    def has_custom_fees(self) -> bool:
        """Foxify vaults do not have explicit deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee.

        Fees are internalised in the share price.

        :return:
            0.0 as fees are built into returns
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee.

        Fees are internalised in the share price.

        :return:
            0.0 as fees are built into returns
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """No lock-up period for Foxify vaults."""
        return None

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Report Foxify's direct redemption timing.

        The Foxify LP vault adapter has no withdrawal request, cooldown, or
        epoch mechanism. See the `Foxify documentation
        <https://docs.foxify.trade/>`__.

        :return:
            A zero-length ``instant`` period.
        """
        return INSTANT_WITHDRAWAL_PERIOD

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link."""
        return "https://www.foxify.trade/"
