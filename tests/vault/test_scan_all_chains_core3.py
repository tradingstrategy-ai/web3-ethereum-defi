"""Test Core3 wiring in the all-chains scanner."""

import datetime
import logging
from pathlib import Path

import pytest

from eth_defi.currency_api.constants import CURRENCY_API_DATABASE, DEFAULT_QUOTE_CURRENCIES
from eth_defi.currency_api.scanner import ScanResult
from eth_defi.vault import scan_all_chains
from eth_defi.vault.scan_all_chains import ChainResult

EXPECTED_TEST_ROW_COUNT = 12
CURRENCY_SOURCE_DOWN_ERROR = "currency source down"


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
        scan_currency_rates=False,
    )

    assert protocols == ["Core3"]


def test_currency_rates_are_scheduled_by_default():
    """Currency rates are default-on because the data source needs no API key."""
    assert scan_all_chains.should_scan_currency_rates(skip_currency_rates=False) is True

    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=False,
        scan_currency_rates=True,
    )

    assert protocols == [scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME]


def test_skip_currency_rates_disables_currency_rates(caplog: pytest.LogCaptureFixture):
    """``SKIP_CURRENCY_RATES=true`` disables currency-rate scans."""
    caplog.set_level(logging.INFO)

    assert scan_all_chains.should_scan_currency_rates(skip_currency_rates=True) is False
    assert "SKIP_CURRENCY_RATES=true" in caplog.text


def test_currency_rate_helpers_read_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Currency-rate helper functions honour prefixed environment variables.

    Steps:

    1. Assert defaults are used when no environment variables are configured.
    2. Assert prefixed variables override standalone ``scan-currencies`` names.
    3. Assert dates are parsed as ``datetime.date``.
    """
    for name in (
        "CURRENCY_API_DB_PATH",
        "CURRENCY_API_DATABASE_PATH",
        "CURRENCY_API_QUOTE_CURRENCIES",
        "QUOTE_CURRENCIES",
        "CURRENCY_API_START_DATE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert scan_all_chains.resolve_currency_api_database_path() == CURRENCY_API_DATABASE
    assert scan_all_chains.resolve_currency_api_database_path(tmp_path) == tmp_path / "exchange-rates.duckdb"
    assert scan_all_chains._read_currency_quote_currencies() == DEFAULT_QUOTE_CURRENCIES
    assert scan_all_chains._parse_optional_date_env("CURRENCY_API_START_DATE") is None

    monkeypatch.setenv("QUOTE_CURRENCIES", "eur, gbp")
    monkeypatch.setenv("CURRENCY_API_QUOTE_CURRENCIES", "JPY, aud, btc")
    monkeypatch.setenv("CURRENCY_API_DATABASE_PATH", str(tmp_path / "fallback.duckdb"))
    monkeypatch.setenv("CURRENCY_API_DB_PATH", str(tmp_path / "rates.duckdb"))
    monkeypatch.setenv("CURRENCY_API_START_DATE", "2026-06-30")

    assert scan_all_chains._read_currency_quote_currencies() == ("jpy", "aud", "btc")
    assert scan_all_chains.resolve_currency_api_database_path(tmp_path) == tmp_path / "rates.duckdb"
    assert scan_all_chains._parse_optional_date_env("CURRENCY_API_START_DATE") == datetime.date(2026, 6, 30)


def test_currency_rates_have_24h_default_cycle():
    """Currency rates keep a daily cycle even if other items use a shorter default."""
    cycle_overrides = scan_all_chains.ensure_default_scan_cycles({})

    _, due_protocols = scan_all_chains.get_due_items(
        chain_configs=[],
        native_protocols=[scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME],
        cycle_overrides=cycle_overrides,
        default_cycle=datetime.timedelta(hours=1),
        state={
            scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME: (scan_all_chains.native_datetime_utc_now() - datetime.timedelta(hours=23)).isoformat(),
        },
    )

    assert due_protocols == []

    _, due_protocols = scan_all_chains.get_due_items(
        chain_configs=[],
        native_protocols=[scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME],
        cycle_overrides=cycle_overrides,
        default_cycle=datetime.timedelta(hours=1),
        state={
            scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME: (scan_all_chains.native_datetime_utc_now() - datetime.timedelta(hours=25)).isoformat(),
        },
    )

    assert due_protocols == [scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME]


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
            return EXPECTED_TEST_ROW_COUNT

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
    assert result.vault_count == EXPECTED_TEST_ROW_COUNT
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
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
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
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
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


def test_scan_currency_rates_fn_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The currency-rate wrapper maps scanner metrics to a best-effort result.

    Steps:

    1. Mock the currency API incremental scanner to return a closeable DB.
    2. Run the wrapper.
    3. Assert the row count is exposed and the DB handle is closed.
    """

    class FakeDb:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_db = FakeDb()

    def fake_currency_run_incremental_scan(**_: object) -> ScanResult:
        return ScanResult(
            db=fake_db,
            dates_requested=2,
            rows_upserted=EXPECTED_TEST_ROW_COUNT,
            dates_unavailable=1,
            transient_failures=1,
        )

    monkeypatch.setattr(scan_all_chains, "currency_run_incremental_scan", fake_currency_run_incremental_scan)

    result = scan_all_chains.scan_currency_rates_fn(
        db_path=tmp_path / "exchange-rates.duckdb",
        max_workers=2,
    )

    assert result.status == "success"
    assert result.vault_scan_ok is True
    assert result.price_scan_ok is None
    assert result.price_rows == EXPECTED_TEST_ROW_COUNT
    assert result.error == "1 transient currency rate failures ignored"
    assert fake_db.closed is True


def test_scan_currency_rates_fn_ignores_scanner_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Underlying currency API failures are downgraded inside the wrapper."""

    def fake_currency_run_incremental_scan(**_: object) -> ScanResult:
        raise RuntimeError(CURRENCY_SOURCE_DOWN_ERROR)

    monkeypatch.setattr(scan_all_chains, "currency_run_incremental_scan", fake_currency_run_incremental_scan)

    result = scan_all_chains.scan_currency_rates_fn(
        db_path=tmp_path / "exchange-rates.duckdb",
        max_workers=2,
    )

    assert result.status == "success"
    assert result.vault_scan_ok is True
    assert result.price_scan_ok is None
    assert f"ignored failure: {CURRENCY_SOURCE_DOWN_ERROR}" in result.error
    assert f"RuntimeError: {CURRENCY_SOURCE_DOWN_ERROR}" in result.traceback_str


def test_run_scan_tick_ignores_currency_rate_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Currency rate failures do not block the all-chain scan cycle.

    Steps:

    1. Mock the currency-rate wrapper to raise an unexpected error.
    2. Run a scan tick with only currency rates active.
    3. Assert the item is treated as successful and its cycle state is advanced.
    """
    saved_items: list[str] = []

    def fake_scan_currency_rates_fn(**_: object) -> ChainResult:
        raise RuntimeError(CURRENCY_SOURCE_DOWN_ERROR)

    monkeypatch.setattr(scan_all_chains, "scan_currency_rates_fn", fake_scan_currency_rates_fn)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_, **__: None)

    results = scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=[scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=False,
        scan_currency_rates=True,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
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
        currency_api_db_path=tmp_path / "exchange-rates.duckdb",
        on_item_success=saved_items.append,
    )

    result = results[scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME]
    assert result.status == "success"
    assert "ignored failure" in result.error
    assert saved_items == [scan_all_chains.CURRENCY_RATES_PROTOCOL_NAME]


def test_run_scan_tick_continues_when_vault_settlement_scan_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Vault settlement failures are reported without blocking exports.

    Steps:

    1. Mock one EVM chain scan to succeed.
    2. Mock its per-chain settlement scanner to reproduce the Linea historical
       ``extraData`` failure.
    3. Assert no settlement dashboard row is shown, the normal chain row
       carries the error, and post-processing still runs.
    """
    post_processing_calls: list[dict[str, object]] = []
    dashboard_snapshots: list[tuple[list[str], dict[str, tuple[str, str | None]]]] = []
    error_message = "Linea historical extraData is 97 bytes"
    settlement_result_name = "Linea settlements"

    def fake_scan_chain(*_: object, **__: object) -> ChainResult:
        return ChainResult(
            name="Linea",
            status="success",
            vault_scan_ok=True,
            price_scan_ok=True,
            end_block=123,
            chain_id=59144,
            rpc_url="https://linea.example",
        )

    def fake_scan_chain_vault_settlements(**_: object) -> ChainResult:
        return ChainResult(
            name=settlement_result_name,
            status="failed",
            error=error_message,
            traceback_str=f"RuntimeError: {error_message}",
        )

    def fake_run_post_processing(**kwargs: object) -> dict[str, bool]:
        post_processing_calls.append(kwargs)
        return {"clean-prices": True, "export-top-vaults-json": True}

    def fake_print_dashboard(results: dict[str, ChainResult], display_order: list[str] | None = None, **_: object) -> None:
        dashboard_snapshots.append(
            (
                list(display_order or []),
                {name: (result.status, result.error) for name, result in results.items()},
            )
        )

    monkeypatch.setattr(scan_all_chains, "scan_chain", fake_scan_chain)
    monkeypatch.setattr(scan_all_chains, "scan_chain_vault_settlements", fake_scan_chain_vault_settlements)
    monkeypatch.setattr(scan_all_chains.VaultDatabase, "read", staticmethod(lambda _path: object()))
    monkeypatch.setattr(scan_all_chains, "run_post_processing", fake_run_post_processing)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", fake_print_dashboard)

    results = scan_all_chains.run_scan_tick(
        chains=[
            scan_all_chains.ChainConfig(
                name="Linea",
                env_var="JSON_RPC_LINEA",
                scan_vaults=True,
            )
        ],
        active_protocols=[],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=False,
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
        cleaned_price_path=tmp_path / "cleaned-vault-prices-1h.parquet",
        settlement_db_path=tmp_path / "vault-settlements.duckdb",
        scan_vault_settlements=True,
    )

    assert settlement_result_name not in results
    linea_result = results["Linea"]
    assert linea_result.status == "success"
    assert "Settlement scan failed" in linea_result.error
    assert "Linea historical extraData" in linea_result.error
    assert f"RuntimeError: {error_message}" in linea_result.traceback_str
    assert post_processing_calls
    assert post_processing_calls[0]["settlement_db_path"] == tmp_path / "vault-settlements.duckdb"

    assert all(settlement_result_name not in display_order for display_order, _ in dashboard_snapshots)
    assert any(statuses["Linea"][0] == "success" and statuses["Linea"][1] and "Linea historical extraData" in statuses["Linea"][1] for _, statuses in dashboard_snapshots)
    captured = capsys.readouterr()
    assert "Full tracebacks for failed scans" in captured.out
    assert f"RuntimeError: {error_message}" in captured.out
