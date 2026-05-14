"""Regression: the GMX adapter no longer runs a pre-flight backtest data
validator. Missing/partial feather files for any pair in pair_whitelist
must not abort a backtest run — Freqtrade's own data loader handles
gaps natively and the pair simply starts trading when its candles arrive.

Spec: docs/superpowers/specs/2026-05-14-gmx-backtest-no-data-validator-design.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: Source path of the freqtrade adapter, relative to the repo root.
GMX_EXCHANGE_PY = Path(__file__).resolve().parents[3] / "eth_defi" / "gmx" / "freqtrade" / "gmx_exchange.py"


# --------------------------------------------------------------------------- #
# Source-text guard — works without `freqtrade` installed in the venv.
# This is the authoritative locally-verifiable assertion.
# --------------------------------------------------------------------------- #


def test_validator_method_definitions_are_removed_from_source():
    """The validator chain must remain physically absent from the
    adapter source. Anyone re-adding a pre-flight data gate will trip
    this test and be redirected to the spec.
    """
    src = GMX_EXCHANGE_PY.read_text(encoding="utf-8")

    forbidden = [
        "def _validate_backtest_timerange",
        "def _validate_pair_data",
        "def _parse_timerange_date",
        "self._validate_backtest_timerange(",
        "InsufficientHistoricalDataError",
    ]
    offenders = [token for token in forbidden if token in src]
    assert not offenders, f"Removed validator artefacts reappeared in {GMX_EXCHANGE_PY.name}: {offenders}. See spec 2026-05-14-gmx-backtest-no-data-validator-design.md."


# --------------------------------------------------------------------------- #
# Import-based guard — runs only when freqtrade is installed alongside the
# adapter (the GMX Tests CI workflow).  In the bare web3-ethereum-defi venv
# this will skip gracefully.
# --------------------------------------------------------------------------- #


def test_validator_methods_are_removed_from_class():
    """Belt-and-braces guard: when the full adapter import chain works,
    the Gmx class also exposes none of the removed helpers as bound
    methods.
    """
    pytest.importorskip("freqtrade.enums", reason="freqtrade.enums required (install with --extras freqtrade)")

    from eth_defi.gmx.freqtrade import gmx_exchange

    cls = gmx_exchange.Gmx
    assert not hasattr(cls, "_validate_backtest_timerange")
    assert not hasattr(cls, "_validate_pair_data")
    assert not hasattr(cls, "_parse_timerange_date")
    assert not hasattr(gmx_exchange, "InsufficientHistoricalDataError")
