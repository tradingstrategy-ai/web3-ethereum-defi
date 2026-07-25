"""Test Xerberus wiring in the all-chains scanner."""

import logging
from pathlib import Path

import pytest

from eth_defi.vault import scan_all_chains
from eth_defi.vault.scan_all_chains import ChainResult


def test_xerberus_is_scheduled_with_credentials():
    """Xerberus is scheduled when key and email are configured."""
    scan_xerberus = scan_all_chains.should_scan_xerberus(
        skip_xerberus=False,
        xerberus_api_key="test-key",
        xerberus_api_email="ops@example.com",
    )
    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        scan_xerberus=scan_xerberus,
    )
    assert protocols == ["Xerberus"]


def test_skip_xerberus_disables():
    assert (
        scan_all_chains.should_scan_xerberus(
            skip_xerberus=True,
            xerberus_api_key="test-key",
            xerberus_api_email="ops@example.com",
        )
        is False
    )


def test_missing_xerberus_api_key_disables_with_warning(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    assert (
        scan_all_chains.should_scan_xerberus(
            skip_xerberus=False,
            xerberus_api_key=None,
            xerberus_api_email="ops@example.com",
        )
        is False
    )
    assert "XERBERUS_API_KEY is not set" in caplog.text


def test_missing_xerberus_email_disables_with_warning(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    assert (
        scan_all_chains.should_scan_xerberus(
            skip_xerberus=False,
            xerberus_api_key="test-key",
            xerberus_api_email=None,
        )
        is False
    )
    assert "XERBERUS_API_EMAIL is not set" in caplog.text
    assert "do not invent" in caplog.text


def test_scan_xerberus_fn_closes_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """scan_xerberus_fn always closes the DuckDB handle."""

    class FakeDB:
        def __init__(self):
            self.closed = False

        def get_entity_counts(self):
            return {"distinct_pools": 3, "distinct_protocols": 2}

        def close(self):
            self.closed = True

    fake_db = FakeDB()

    def fake_scan(**kwargs):
        return fake_db

    monkeypatch.setattr(scan_all_chains, "create_xerberus_session", lambda **_: object())
    monkeypatch.setattr(scan_all_chains, "xerberus_scan", fake_scan)

    result = scan_all_chains.scan_xerberus_fn(xerberus_db_path=tmp_path / "x.duckdb")
    assert result.status == "success"
    assert result.vault_count == 5
    assert fake_db.closed is True


def test_run_scan_tick_updates_xerberus_cycle_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Successful Xerberus scan advances cycle state via on_item_success."""

    def fake_scan_xerberus_fn(**_: object) -> ChainResult:
        return ChainResult(name="Xerberus", status="success", vault_count=1, vault_scan_ok=True)

    monkeypatch.setattr(scan_all_chains, "scan_xerberus_fn", fake_scan_xerberus_fn)

    successes: list[str] = []

    results = scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=["Xerberus"],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        scan_xerberus=True,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=1,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "v.pickle",
        uncleaned_price_path=tmp_path / "p.parquet",
        reader_state_path=tmp_path / "r.pickle",
        hyperliquid_db_path=tmp_path / "hl.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hlhf.duckdb",
        grvt_db_path=tmp_path / "g.duckdb",
        lighter_db_path=tmp_path / "l.duckdb",
        hibachi_db_path=tmp_path / "h.duckdb",
        apex_db_path=tmp_path / "a.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "bkp",
        on_item_success=successes.append,
        xerberus_db_path=tmp_path / "x.duckdb",
    )

    assert results["Xerberus"].status == "success"
    assert successes == ["Xerberus"]
