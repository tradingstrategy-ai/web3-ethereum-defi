"""Untangle Finance vault support."""

from dataclasses import dataclass
from functools import cached_property
import logging

from web3.contract import Contract
from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract

from eth_defi.erc_7540.vault import ERC7540Vault

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModuleAddressMap:
    """See Vault.sol"""

    withdrawModule: HexAddress
    valuationModule: HexAddress
    authModule: HexAddress
    feeModule: HexAddress
    crosschainModule: HexAddress


class UntangleVault(ERC7540Vault):
    """Untangle Finance vaults.

    - ERC-7540 custom asynchronous redemption
    - There is a fee module, but currently fees are not exposed through the smart contract API or the web interface

    More information:

    - `Github <https://github.com/untangledfinance/untangled-vault/blob/dev/contracts/Vault.sol>`__
    - `Example vault <https://arbiscan.io/address/0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9#code.`__
    - `Web app vault <<https://app.untangled.finance/vault/arbitrum/0x4a3F7Dd63077cDe8D7eFf3C958EB69A3dD7d31a9>`__
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="untangle/Vault.json",
        )

    def fetch_modules(self) -> ModuleAddressMap:
        addresses = self.vault_contract.functions.getModules().call()
        return ModuleAddressMap(*addresses)

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0
