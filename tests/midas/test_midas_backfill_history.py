"""Regression tests for the Midas historical backfill helpers."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
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
    tmp_path: Path,
) -> None:
    """Start a default Midas rewrite at its earliest selected deployment."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert (
        backfill_history_module.resolve_price_scan_start_block(
            [MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM],
            timestamp_cache_folder=tmp_path,
        )
        == MIDAS_MTBILL_ETHEREUM.first_seen_at_block
    )


def test_resolve_price_scan_start_block_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Allow an operator to pin a smaller diagnostic backfill range."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))

    assert (
        backfill_history_module.resolve_price_scan_start_block(
            [MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM],
            timestamp_cache_folder=tmp_path,
        )
        == EXPLICIT_START_BLOCK
    )


def test_resolve_price_scan_start_block_uses_first_supported_cache_block(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Clip a Midas history rewrite to the first timestamp cache block.

    A sparse cache can be valid for its own existing range but cannot answer a
    request before its first block.  The backfill must therefore begin at that
    supported boundary instead of failing while looking up its first timestamp.
    """

    monkeypatch.delenv("START_BLOCK", raising=False)
    first_cached_block = MIDAS_MTBILL_ETHEREUM.first_seen_at_block + 1_000
    cache = BlockTimestampDatabase.create(MIDAS_MTBILL_ETHEREUM.chain_id, tmp_path)
    try:
        cache.import_chain_data(
            MIDAS_MTBILL_ETHEREUM.chain_id,
            pd.Series(data=[1_700_000_000], index=[first_cached_block]),
        )
    finally:
        cache.close()

    assert (
        backfill_history_module.resolve_price_scan_start_block(
            [MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM],
            timestamp_cache_folder=tmp_path,
        )
        == first_cached_block
    )


def test_resolve_frequency_defaults_to_daily(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Use daily historical samples unless an operator explicitly overrides them.

    1. Clear the optional operator override.
    2. Resolve the script frequency.
    3. Assert that the documented daily default is used.
    """

    # 1. Clear the optional operator override.
    monkeypatch.delenv("FREQUENCY", raising=False)

    # 2. Resolve the script frequency.
    # 3. Assert that the documented daily default is used.
    assert backfill_history_module.resolve_frequency() == "1d"


def test_resolve_frequency_honours_hourly_override(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Allow an operator to opt into an hourly Midas historical backfill.

    1. Set the explicit hourly operator override.
    2. Resolve the script frequency.
    3. Assert that the requested frequency is retained.
    """

    # 1. Set the explicit hourly operator override.
    monkeypatch.setenv("FREQUENCY", "1h")

    # 2. Resolve the script frequency.
    # 3. Assert that the requested frequency is retained.
    assert backfill_history_module.resolve_frequency() == "1h"
