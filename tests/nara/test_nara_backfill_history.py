"""Regression tests for Nara historical-backfill helpers."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated Nara backfill script as a Python module."""
    script_path = Path(__file__).parents[2] / "scripts" / "nara" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("nara_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_frequency_defaults_to_hourly(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Use hourly samples because the default output parquet is hourly."""
    monkeypatch.delenv("FREQUENCY", raising=False)

    assert backfill_history_module.resolve_frequency() == "1h"
