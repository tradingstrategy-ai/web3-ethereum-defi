"""Morpho vault protocol support.

.. deprecated::
    This module has been moved to :py:mod:`eth_defi.erc_4626.vault_protocol.morpho`.
    Imports from this location are maintained for backwards compatibility.

- `See an example vault here <https://app.gauntlet.xyz/vaults/eth:0x4881ef0bf6d2365d3dd6499ccd7532bcdbce0658>`__
- `Example contract <https://basescan.org/address/0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844#readContract>`__
"""

from eth_defi.morpho.vault import (
    MorphoV1Vault,
    MorphoV1VaultHistoricalReader,
    MorphoVault,
    MorphoVaultHistoricalReader,
)

__all__ = [
    "MorphoV1Vault",
    "MorphoV1VaultHistoricalReader",
    "MorphoVault",
    "MorphoVaultHistoricalReader",
]
