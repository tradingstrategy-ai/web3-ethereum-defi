"""Backwards-compatible import shim for the IPOR deposit manager.

The implementation lives in
:mod:`eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem`.
"""

# ruff: noqa: E402, F403

import warnings

warnings.warn(
    "eth_defi.ipor.deposit_redeem is deprecated, use eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem instead",
    DeprecationWarning,
    stacklevel=2,
)

from eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem import *
