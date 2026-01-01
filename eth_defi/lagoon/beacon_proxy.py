"""Backwards-compatible import shim for eth_defi.lagoon.beacon_proxy.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.beacon_proxy is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy import *  # noqa: F401, F403
