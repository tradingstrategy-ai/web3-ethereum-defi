"""GMX Freqtrade integration module.

This module provides monkeypatches to enable GMX exchange support in Freqtrade,
along with sensitive data filtering for log redaction.
"""

from eth_defi.gmx.freqtrade.gmx_exchange import Gmx
from eth_defi.gmx.freqtrade.monkeypatch import patch_freqtrade
from eth_defi.gmx.freqtrade.sensitive_filter import (
    SensitiveDataFilter,
    is_logging_patched,
    patch_logging,
    unpatch_logging,
)

__all__ = [
    "Gmx",
    "patch_freqtrade",
    "SensitiveDataFilter",
    "patch_logging",
    "unpatch_logging",
    "is_logging_patched",
]
