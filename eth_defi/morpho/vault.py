"""Morpho vault reading implementation.

.. deprecated::
    This module has been moved to :py:mod:`eth_defi.erc_4626.vault_protocol.morpho.vault_v1`.
    Imports from this location are maintained for backwards compatibility.
"""

# Re-export from new location for backwards compatibility
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import (
    MorphoV1Vault,
    MorphoV1VaultHistoricalReader,
    MorphoVault,
    MorphoVaultHistoricalReader,
)

__all__ = [
    "MorphoV1Vault",
    "MorphoV1VaultHistoricalReader",
    # Backwards compatibility aliases
    "MorphoVault",
    "MorphoVaultHistoricalReader",
]
