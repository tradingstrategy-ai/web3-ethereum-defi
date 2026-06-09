"""Test Core3 wiring in the all-chains scanner."""

import datetime
import logging
from pathlib import Path

import pytest

from eth_defi.vault import scan_all_chains
from eth_defi.vault.scan_all_chains import ChainResult


def test_core3_is_scheduled_by_default_with_api_key():
    """Core3 is default-on when ``CORE3_API_KEY`` is configured."""
    scan_core3 = scan_all_chains.should_scan_core3(
        skip_core3=False,
        core3_api_key="core3_test",
    )

    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=scan_core3,
    )

    assert protocols == ["Core3"]


def test_skip_core3_disables_core3():
    """``SKIP_CORE3=true`` disables Core3 even if an API key exists."""
    assert scan_all_chains.should_scan_core3(skip_core3=True, core3_api_key="core3_test") is False


def test_missing_core3_api_key_disables_core3_with_warning(caplog: pytest.LogCaptureFixture):
    """Missing ``CORE3_API_KEY`` logs a warning and disables Core3."""
    caplog.set_level(logging.WARNING)

    scan_core3 = scan_all_chains.should_scan_core3(skip_core3=False, core3_api_key=None)

    assert scan_core3 is False
    assert "CORE3_API_KEY is not set" in caplog.text


def test_get_due_items_honours_core3_cycle():
    """Cycle scheduler treats Core3 like the other non-EVM scheduled items."""
    state = {
        "Core3": (scan_all_chains.native_datetime_utc_now() - datetime.timedelta(hours=23)).isoformat(),
    }

    _, due_protocols = scan_all_chains.get_due_items(
        chain_configs=[],
        native_protocols=["Core3"],
        cycle_overrides=scan_all_chains.parse_scan_cycles("Core3=24h"),
        default_cycle=datetime.timedelta(hours=1),
        state=state,
    )
    assert due_protocols == []

    state["Core3"] = (scan_all_chains.native_datetime_utc_now() - datetime.timedelta(hours=25)).isoformat()
    _, due_protocols = scan_all_chains.get_due_items(
        chain_configs=[],
        native_protocols=["Core3"],
        cycle_overrides=scan_all_chains.parse_scan_cycles("Core3=24h"),
        default_cycle=datetime.timedelta(hours=1),
        state=state,
    )
    assert due_protocols == ["Core3"]


def test_scan_core3_fn_closes_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The Core3 wrapper closes the DuckDB handle before export can run.

    Steps:

    1. Mock the Core3 project scan to return a closeable database handle.
    2. Run the scanner wrapper against a temporary DuckDB path.
    3. Assert the handle was closed and price scanning is marked not applicable.
    """

    class FakeDb:
        closed = False

        def get_project_count(self) -> int:
            return 12

        def close(self) -> None:
            self.closed = True

    fake_db = FakeDb()

    def fake_scan_projects(**_: object) -> FakeDb:
        return fake_db

    monkeypatch.setattr(scan_all_chains, "create_core3_session", lambda **_: object())
    monkeypatch.setattr(scan_all_chains, "core3_scan_projects", fake_scan_projects)

    result = scan_all_chains.scan_core3_fn(
        core3_db_path=tmp_path / "core3.duckdb",
        max_workers=3,
        fetch_sections=False,
    )

    assert result.status == "success"
    assert result.vault_count == 12
    assert result.price_scan_ok is None
    assert fake_db.closed is True


def test_run_scan_tick_updates_core3_cycle_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A successful Core3 tick invokes the cycle-state success callback.

    Steps:

    1. Mock the Core3 wrapper to return a successful enrichment result.
    2. Run a scan tick with only Core3 active and post-processing disabled.
    3. Assert Core3 succeeded and its cycle-state callback fired once.
    """
    saved_items: list[str] = []

    def fake_scan_core3_fn(**_: object) -> ChainResult:
        return ChainResult(
            name="Core3",
            status="success",
            vault_scan_ok=True,
            price_scan_ok=None,
            vault_count=12,
        )

    monkeypatch.setattr(scan_all_chains, "scan_core3_fn", fake_scan_core3_fn)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_, **__: None)

    results = scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=["Core3"],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=True,
        max_workers=1,
        core3_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        core3_db_path=tmp_path / "core3.duckdb",
        on_item_success=saved_items.append,
    )

    assert results["Core3"].status == "success"
    assert saved_items == ["Core3"]


def test_run_scan_tick_fetches_core3_sections_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Core3 detailed section fetching is enabled by default.

    Steps:

    1. Mock the Core3 wrapper and capture its keyword arguments.
    2. Run a scan tick with only Core3 active.
    3. Assert ``fetch_sections`` defaults to ``True``.
    """
    captured_kwargs: dict[str, object] = {}

    def fake_scan_core3_fn(**kwargs: object) -> ChainResult:
        captured_kwargs.update(kwargs)
        return ChainResult(
            name="Core3",
            status="success",
            vault_scan_ok=True,
            price_scan_ok=None,
            vault_count=12,
        )

    monkeypatch.setattr(scan_all_chains, "scan_core3_fn", fake_scan_core3_fn)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_, **__: None)

    scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=["Core3"],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=True,
        max_workers=1,
        core3_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        core3_db_path=tmp_path / "core3.duckdb",
    )

    assert captured_kwargs["fetch_sections"] is True
