"""Superform vault support."""

from functools import cached_property
import logging

from web3.contract import Contract

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class SuperformVault(ERC4626Vault):
    """Superform vaults.

    SuperVaults offer a simple way to get the best yields onchain without needing to check rates or rebalance your portfolio.

    Interest is primarily generated through fixed-rate and variable-rate onchain lending products, but it can be generated in other ways. It is important to research every opportunity before making a deposit to ensure you are making an informed decision. To view more details about any given strategy, select it to view the vault detail page.

    - See vaults here https://app.superform.xyz/
    - `Github <https://github.com/superform-xyz/v2-core>`__
    - `X <https://app.superform.xyz/>`
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.elevated

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """TODO: Unsure"""
        return 0

    def get_link(self, referral: str | None = None) -> str:
        return f"https://app.superform.xyz/vault/{self.chain_id}_{self.vault_address_checksumless}"
