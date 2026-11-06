"""Smoke test for the GMX Freqtrade patched entrypoint.

Importing :mod:`eth_defi.gmx.freqtrade.patched_entrypoint` is the startup
path used by the trading wrappers. This test checks that the import installs
the GMX bridge.

Verified on Python 3.12 with ``freqtrade==2026.9`` and ``ccxt==4.5.84``.
The test does not require those exact versions, so a checkout that has
another freqtrade release still fails if the patch cannot install.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip(
    "freqtrade.enums",
    reason="freqtrade.enums required for the GMX entrypoint",
)
pytest.importorskip("ccxt", reason="ccxt required for the GMX entrypoint")


def test_patched_entrypoint_installs_gmx() -> None:
    """Importing the entrypoint registers async GMX on CCXT and freqtrade.

    :returns: None. Raises ``RuntimeError`` from the entrypoint when the
        async GMX class did not install.
    """
    import ccxt.async_support
    from freqtrade.exchange import common

    import eth_defi.gmx.freqtrade.patched_entrypoint as entry

    assert entry._patched_at_module_level is True
    assert inspect.iscoroutinefunction(ccxt.async_support.gmx.load_markets)
    assert "gmx" in common.SUPPORTED_EXCHANGES
