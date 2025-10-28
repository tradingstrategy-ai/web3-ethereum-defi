"""Plutus hedge token vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class PlutusVault(ERC4626Vault):
    """Plutus vaults.

    - Hedge token vaults: https://plutus.fi/Vaults
    - Docs: https://docs.plutusdao.io/plutus-docs
    - About plHEDGE vault: https://medium.com/@plutus.fi/introducing-plvhedge-an-automated-funding-arbitrage-vault-f2f222fa8c56
    """

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Hardcoded PLutus fees.

        https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Hardcoded PLutus fees.

        https://docs.plutusdao.io/plutus-docs/protocol-fees
        """
        return 0.12

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Currently Plutus vaults are manually opened/closed/"""
        # if !withdrawalsPaused && fundsInTrading==0: contract open
        return None
