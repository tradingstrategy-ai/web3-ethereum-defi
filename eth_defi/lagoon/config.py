"""Backwards-compatible import shim for eth_defi.lagoon.config.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.config.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.config is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.config instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.config import *  # noqa: F401, F403
