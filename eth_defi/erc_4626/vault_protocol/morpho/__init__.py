"""Morpho protocol integration.

This module provides support for both Morpho vault versions:

- :py:mod:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1` - Morpho V1 (MetaMorpho)
  vaults that directly integrate with Morpho markets

- :py:mod:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v2` - Morpho V2 vaults
  with adapter-based architecture for multi-protocol yield allocation
"""

from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import (
    MorphoV1Vault,
    MorphoV1VaultHistoricalReader,
    MorphoVault,
    MorphoVaultHistoricalReader,
)
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault

__all__ = [
    "MorphoV1Vault",
    "MorphoV1VaultHistoricalReader",
    "MorphoV2Vault",
    # Backwards compatibility
    "MorphoVault",
    "MorphoVaultHistoricalReader",
]
