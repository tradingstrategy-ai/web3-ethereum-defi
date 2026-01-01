"""TrueFi vault support."""

from functools import cached_property
import logging

from web3.contract import Contract

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import VaultTechnicalRisk

logger = logging.getLogger(__name__)


class TrueFiVault(ERC4626Vault):
    """TrueFI vaults.

    TrueFi Lines of Credit (also referred to as Automated Lines of Credit or "ALOCs”) are lending pools for a single borrower, where the interest rate paid by borrowers is determined by a configurable interest rate curve.

    - Unsecured lending protocol
    - Market makers as customers
    - See vaults here https://app.truefi.io/
    - `Docs <https://docs.truefi.io/faq/>`__
    """

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.elevated

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="truefi/AutomatedLineOfCredit.json",
        )

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Protocol fee.

        - Now hardcoded.
        """
        return 0.005

    def get_link(self, referral: str | None = None) -> str:
        return f"https://app.truefi.io/vault/aloc/{self.chain_id}/{self.vault_address}"
