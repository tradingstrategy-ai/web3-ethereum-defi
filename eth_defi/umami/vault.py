"""Umami gmUSDC vault support."""

from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class UmamiVault(ERC4626Vault):
    """Umami vaults.

    - GMUSDC, etc: https://umami.finance/vaults/arbitrum/gm/gmusdc

    Umami vaults do not have open source Github repository, developer documentation or easy developer access for integrations,
    making it not recommended to deal with them.
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.extra_high

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="umami/AssetVault.json",
        )

    def fetch_aggregate_vault(self) -> Contract:
        addr = self.vault_contract.functions.aggregateVault().call()
        return get_deployed_erc_4626_contract(
            self.web3,
            addr,
            abi_fname="umami/AggregateVault.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Umami fees hardcoded because no transparent development/onchain accessors.

        https://umami.finance/vaults/arbitrum/gm/gmusdc
        """
        return 0.02

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Umami fees hardcoded because no transparent development/onchain accessors.

        https://umami.finance/vaults/arbitrum/gm/gmusdc
        """
        return 0.20
