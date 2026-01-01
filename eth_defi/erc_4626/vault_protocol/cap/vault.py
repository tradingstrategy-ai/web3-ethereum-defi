"""Covered Agent Protocol (CAP) vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault

logger = logging.getLogger(__name__)


class CAPVault(YearnV3Vault):
    """Covered Agent Protocol (CAP) vaults.

    - CAP is a covered call yield protocol
    - Uses Yearn V3 vault infrastructure under the hood
    - Vaults are identifiable by the "cap " prefix in their name
    - Example vault: https://etherscan.io/address/0x3ed6aa32c930253fc990de58ff882b9186cd0072
    """

    def has_custom_fees(self) -> bool:
        """CAP vaults do not have explicit deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee.

        Fees are internalised in the share price.

        :return:
            0.0 as fees are built into returns
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee.

        Fees are internalised in the share price.

        :return:
            0.0 as fees are built into returns
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link."""
        return f"https://cap.app/asset/1/USDC"
