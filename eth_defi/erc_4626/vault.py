from functools import cached_property

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.vault.base import VaultBase, VaultSpec, VaultInfo


class ERC4626VaultInfo(VaultInfo):
    """Capture information about ERC- vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    address: HexAddress




class ERC4626Vault(VaultBase):
    """ERC-4626 vault adapter

    - Metadata
    - Deposit and redeem from the vault
    - Vault price reader
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
    ):
        self.web3 = web3
        self.spec = spec


    @property
    def chain_id(self) -> int:
        return self.spec.chain_id

    @cached_property
    def vault_address(self) -> HexAddress:
        return Web3.to_checksum_address(self.spec.vault_address)

    @property
    def name(self) -> str:
        return self.share_token.name

    @property
    def symbol(self) -> str:
        return self.share_token.symbol

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_contract(
            self.web3,
            "lagoon/Vault.json",
            self.spec.vault_address,
        )

    @cached_property
    def vault_contract(self) -> Contract:
        """Underlying Vault smart contract."""
        return get_deployed_contract(
            self.web3,
            "lagoon/Vault.json",
            self.spec.vault_address,
        )



