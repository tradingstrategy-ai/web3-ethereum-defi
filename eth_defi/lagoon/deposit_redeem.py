"""Backwards-compatible import shim for eth_defi.lagoon.deposit_redeem.

This module has been moved to eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem.
This shim provides backwards compatibility for existing code.
"""

import warnings

warnings.warn(
    "eth_defi.lagoon.deposit_redeem is deprecated, use eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import *  # noqa: F401, F403
