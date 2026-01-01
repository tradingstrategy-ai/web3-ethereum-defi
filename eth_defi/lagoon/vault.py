"""Backwards-compatible import shim for eth_defi.lagoon.vault.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.vault.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.vault is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.vault instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.vault import *  # noqa: F401, F403
