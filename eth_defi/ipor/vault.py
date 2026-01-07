"""Backwards-compatible import shim for eth_defi.ipor.vault.

This module has been moved to eth_defi.erc_4626.vault_protocol.ipor.vault.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.ipor.vault is deprecated, use eth_defi.erc_4626.vault_protocol.ipor.vault instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.ipor.vault import *  # noqa: F401, F403
