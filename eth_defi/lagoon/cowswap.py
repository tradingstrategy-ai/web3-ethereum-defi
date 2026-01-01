"""Backwards-compatible import shim for eth_defi.lagoon.cowswap.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.cowswap.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.cowswap is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.cowswap instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.cowswap import *  # noqa: F401, F403
