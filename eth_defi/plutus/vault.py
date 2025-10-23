"""Plutus hedge token vault support."""

from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultRisk

logger = logging.getLogger(__name__)


class PlutusVault(ERC4626Vault):
    """Plutus vaults.

    - Hedge token vaults: https://plutus.fi/Vaults
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
