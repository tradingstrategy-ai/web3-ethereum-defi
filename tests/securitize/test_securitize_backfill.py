"""Regression tests for the targeted Securitize backfill helper."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

DEPLOYMENT_BLOCK = 100
FIRST_CACHED_BLOCK = 150
EXPLICIT_START_BLOCK = 75


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated Securitize backfill script as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "securitize" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("securitize_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fetch_contract_deployment_block_uses_binary_search(backfill_history_module) -> None:
    """Locate the first block with runtime code without scanning all blocks."""

    deployment_block = 83
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_code=lambda _address, block_identifier: b"code" if block_identifier >= deployment_block else b"",
        )
    )

    assert backfill_history_module.fetch_contract_deployment_block(web3, "0x0000000000000000000000000000000000000001", 100) == deployment_block


def test_fetch_contract_deployment_block_rejects_non_contract(backfill_history_module) -> None:
    """Reject an address that does not contain runtime code at the scan end."""

    web3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda *_args, **_kwargs: b""))

    with pytest.raises(ValueError, match="No contract code"):
        backfill_history_module.fetch_contract_deployment_block(web3, "0x0000000000000000000000000000000000000001", 100)


def test_resolve_price_scan_start_block_uses_deployment_without_cache(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Start a targeted rewrite at the earliest selected deployment by default."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert backfill_history_module.resolve_price_scan_start_block(1, [200, DEPLOYMENT_BLOCK], timestamp_cache_folder=tmp_path) == DEPLOYMENT_BLOCK


def test_resolve_price_scan_start_block_honours_cache_and_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Respect a usable cache boundary unless the operator specifies a block."""

    cache = BlockTimestampDatabase.create(1, tmp_path)
    try:
        cache.import_chain_data(1, pd.Series(data=[1_700_000_000], index=[FIRST_CACHED_BLOCK]))
    finally:
        cache.close()

    monkeypatch.delenv("START_BLOCK", raising=False)
    assert backfill_history_module.resolve_price_scan_start_block(1, [DEPLOYMENT_BLOCK], timestamp_cache_folder=tmp_path) == FIRST_CACHED_BLOCK

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))
    assert backfill_history_module.resolve_price_scan_start_block(1, [DEPLOYMENT_BLOCK], timestamp_cache_folder=tmp_path) == EXPLICIT_START_BLOCK
