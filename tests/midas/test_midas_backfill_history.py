"""Regression tests for the Midas historical backfill helpers."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.midas.constants import MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM

EXPLICIT_START_BLOCK = 123_456


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated Midas backfill script as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "midas" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("midas_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_price_scan_start_block_uses_earliest_midas_deployment(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Start a default Midas rewrite at its earliest selected deployment."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert (
        backfill_history_module.resolve_price_scan_start_block(
            [MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM],
        )
        == MIDAS_MTBILL_ETHEREUM.first_seen_at_block
    )


def test_resolve_price_scan_start_block_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Allow an operator to pin a smaller diagnostic backfill range."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))

    assert (
        backfill_history_module.resolve_price_scan_start_block(
            [MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM],
        )
        == EXPLICIT_START_BLOCK
    )
