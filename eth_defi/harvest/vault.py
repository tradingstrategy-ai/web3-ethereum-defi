"""Harvest Finance vault support."""

from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultRisk

logger = logging.getLogger(__name__)


class HarvestVault(ERC4626Vault):
    """Harvest vaults.

    - VaultV1 has underlying strategy contract
    - Each vault has only one strategy
    - Uses a custom proxy pattern not supported by Etherscan family explorers

    - https://github.com/harvestfi/harvest-strategy-arbitrum/blob/1e53688004af1b31e64fd569f04bf19ec7d4bc16/contracts/base/VaultV1.sol#L18
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="harvest/VaultV2.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def fetch_strategy(self) -> Contract:
        """Fetch the strategy contract used by this vault."""
        addr = self.vault_contract.strategy().call()
        return addr

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.12
