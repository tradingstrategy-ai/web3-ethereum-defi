"""Backwards-compatible import shim for eth_defi.lagoon.analysis.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.analysis.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.analysis is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.analysis instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.analysis import *  # noqa: F401, F403
