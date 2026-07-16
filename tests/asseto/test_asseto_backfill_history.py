"""Regression tests for the Asseto historical backfill script helpers."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.asseto.constants import ASSETO_AOABT_HASHKEY
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.provider.env import get_json_rpc_env

EXPLICIT_START_BLOCK = 123_456


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated Asseto backfill script as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "asseto" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("asseto_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hashkey_uses_the_standard_rpc_environment_variable() -> None:
    """Expose HashKey Chain through the standard JSON-RPC environment helper."""

    assert get_json_rpc_env(ASSETO_AOABT_HASHKEY.chain_id) == "JSON_RPC_HASHKEY"


def test_resolve_price_scan_start_block_uses_asseto_deployment(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Rewrite Asseto history from the registered product deployment by default."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == ASSETO_AOABT_HASHKEY.first_seen_at_block


def test_resolve_price_scan_start_block_clips_to_timestamp_cache(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Do not ask a sparse local timestamp cache for unavailable early blocks."""

    monkeypatch.delenv("START_BLOCK", raising=False)
    first_cached_block = ASSETO_AOABT_HASHKEY.first_seen_at_block + 1_000
    cache = BlockTimestampDatabase.create(ASSETO_AOABT_HASHKEY.chain_id, tmp_path)
    try:
        cache.import_chain_data(
            ASSETO_AOABT_HASHKEY.chain_id,
            pd.Series(data=[1_700_000_000], index=[first_cached_block]),
        )
    finally:
        cache.close()

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == first_cached_block


def test_resolve_price_scan_start_block_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Allow operators to run a narrowly scoped diagnostic backfill."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == EXPLICIT_START_BLOCK


def test_iter_selected_products_honours_symbol_filter(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Select only the requested Asseto product symbol."""

    monkeypatch.setenv("PRODUCTS", "aoabt")
    monkeypatch.setenv("NETWORKS", "hashkey")

    assert list(backfill_history_module.iter_selected_products()) == [ASSETO_AOABT_HASHKEY]


def test_resolve_frequency_defaults_to_daily(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Use daily samples unless an operator explicitly selects hourly scans."""

    monkeypatch.delenv("FREQUENCY", raising=False)

    assert backfill_history_module.resolve_frequency() == "1d"
