"""Vault-specific management."""
from dataclasses import dataclass
from typing import Collection

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract


@dataclass
class Vault:
    """Wrapped around Enzyme vault."""

    #: Vault smart contract
    #:
    #:
    vault: Contract

    #: Comptroller smart contract
    #:
    #:
    comptroller: Contract

    @property
    def web3(self):
        """Web3 connection.

        Used for reading JSON-RPC calls
        """
        return self.vault.w3

    def get_owner(self) -> HexAddress:
        """Who is the vault owner.

        Vault owner has special priviledges like calling the adapters.

        See `IVaultCore.sol`.
        """
        return self.vault.functions.getOwner()

    def get_name(self) -> str:
        """Get the name of the share token."""
        return self.vault.functions.sharesName()

    def get_symbol(self) -> str:
        """Get the symbol of share tokens.

        See VaultLib.sol.
        """
        return self.vault.functions.symbol().call()

    def get_denomination_asset(self) -> HexAddress:
        """Get the reserve asset for this vault."""
        return self.comptroller.functions.getDenominationAsset().call()

    def get_tracked_assets(self) -> Collection[HexAddress]:
        """Get the list of assets this vault tracks."""
        return self.vault.functions.getTrackedAssets().call()

