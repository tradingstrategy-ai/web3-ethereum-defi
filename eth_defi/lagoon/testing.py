"""Backwards-compatible import shim for eth_defi.lagoon.testing.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.testing.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.testing is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.testing instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.testing import *  # noqa: F401, F403
