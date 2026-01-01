"""Backwards-compatible import shim for eth_defi.lagoon.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon import *  # noqa: F401, F403
