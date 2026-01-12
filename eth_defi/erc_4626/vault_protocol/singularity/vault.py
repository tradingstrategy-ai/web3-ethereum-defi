"""Singularity Finance DynaVault support.

Singularity Finance (SFI) is the on-chain infrastructure powering the AI economy.
The DynaVaults framework implements the ERC4626 vault standard with EIP-5143 slippage protection.

- Homepage: https://singularityfinance.ai/
- Documentation: https://docs.singularityfinance.ai/
- Twitter: https://x.com/singularity_fi
- DynaVaults architecture: https://docs.singularityfinance.ai/sfi-value-proposition/core-pillars-of-the-sfi-l2/sfi-vaults/architecture
- DefiLlama: https://defillama.com/protocol/singularity-finance
- Example vault: https://basescan.org/address/0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805

The DynaVaults framework uses:
- ERC4626 vault standard
- EIP-5143 for slippage protection
- EIP-1167 minimal proxy pattern for gas-efficient deployment
- OpenZeppelin AccessControl for RBAC
- Custom IAM contract for fine-grained access control

Fee structure:
- Management fee and performance fee are available via vault.manager().getFees()
- Deposit and withdrawal fees are configurable but typically kept at 0%
- Fees are internalised in the share price via minting shares to the vault owner
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SingularityVault(ERC4626Vault):
    """Singularity Finance DynaVault.

    DynaVaults are ERC4626-compliant yield-bearing vaults powered by AI-driven strategies.
    They implement EIP-5143 slippage protection to prevent value extraction during vault operations.

    - Homepage: https://singularityfinance.ai/
    - Documentation: https://docs.singularityfinance.ai/
    - Twitter: https://x.com/singularity_fi
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get the Singularity vault contract with full ABI."""
        return get_deployed_contract(
            self.web3,
            fname="singularity/SingularityVault.json",
            address=self.vault_address,
        )

    @cached_property
    def manager_contract(self) -> Contract:
        """Get the Singularity manager contract.

        The manager contract holds fee configuration and other vault parameters.
        """
        manager_address = self.vault_contract.functions.manager().call()
        return get_deployed_contract(
            self.web3,
            fname="singularity/SingularityManager.json",
            address=manager_address,
        )

    def has_custom_fees(self) -> bool:
        """DynaVaults have custom fee getters via the manager contract."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get management fee.

        Fees are read from the manager contract via getFees().

        :return:
            Management fee as a decimal (0.02 = 2%).
        """
        try:
            management_fee, _ = self.manager_contract.functions.getFees().call(block_identifier=block_identifier)
            # Fee is in basis points (100 = 1%)
            return management_fee / 10_000
        except Exception as e:
            logger.warning("Failed to get management fee for Singularity vault %s: %s", self.address, e)
            return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get performance fee.

        Fees are read from the manager contract via getFees().

        :return:
            Performance fee as a decimal (0.10 = 10%).
        """
        try:
            _, performance_fee = self.manager_contract.functions.getFees().call(block_identifier=block_identifier)
            # Fee is in basis points (100 = 1%)
            return performance_fee / 10_000
        except Exception as e:
            logger.warning("Failed to get performance fee for Singularity vault %s: %s", self.address, e)
            return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period.

        DynaVaults support instant redemption with EIP-5143 slippage protection.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault page.

        Returns a link to the Singularity Finance app with the vault address.
        """
        # Singularity uses chain_id and vault address in URL pattern
        return f"https://singularityfinance.ai/"
