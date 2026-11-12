"""Transactional history, admission and native pipeline regression tests."""

import copy
import datetime
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
from filelock import Timeout

from eth_defi.compat import native_datetime_utc_now
from eth_defi.neutral_trade.api import NeutralTradeAPIError, parse_vault
from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID, make_solana_vault_address
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase, ScanResult, run_scan
from eth_defi.neutral_trade.performance import build_performance_summary
from eth_defi.neutral_trade.reporting import render_verification_report
from eth_defi.neutral_trade.vault_data_export import build_raw_prices_dataframe, merge_into_vault_database
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.post_processing import merge_native_protocols
from eth_defi.vault.risk import VAULT_SPECIFIC_RISK, VaultTechnicalRisk
from eth_defi.vault.top_vaults_json import main as export_json
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

# ruff: noqa: PLR2004
ADDRESS = "3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe"


@pytest.fixture
def source(tmp_path: Path) -> Iterator[NeutralTradeDatabase]:
    """Close a real file-backed database even when a test fails.

    Source locks, schema and checkpoint behaviour match the production reader.

    :param tmp_path: Isolated test storage.
    :return: Locked archive fixture.
    """
    with NeutralTradeDatabase(tmp_path / "source.duckdb") as db:
        yield db


@pytest.fixture
def raw_vault() -> dict:
    """Provide public native metadata with explicit denomination and fees.

    Balances and prices in tests are synthetic, never live baseline assertions.

    :return: Canonical directory record.
    """
    return {"bundleKey": ADDRESS, "chain": "solana", "name": "Fixture", "visible": True, "private": False, "enabled": True, "asset": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC", "decimals": 6}, "managementFeeBps": 100, "performanceFeeBps": 2000}


def price_page(cursor: str | None = None, pps: str = "1100000", *, legacy: bool = False) -> dict:
    """Build a deterministic source page with lossless amount strings.

    A stable signature/event identity permits later corrections to replace it.

    :param cursor: Opaque continuation or terminal null.
    :param pps: Raw six-decimal share price.
    :param legacy: Whether source provenance is migrated history.
    :return: Original-envelope-shaped response.
    """
    return {"asOf": {"blockTime": 1794465911}, "data": {"vaultId": ADDRESS, "nextCursor": cursor, "series": [{"blockTime": 1794465911, "pps": pps, "totalEquity": "11000000000", "totalShares": "10000000000", "isLegacy": legacy, "signature": "fixture-event", "slot": 1, "sourceEvent": "OracleUpdated"}]}}


@pytest.mark.parametrize("invalid_field, invalid_value, error", [("blockTime", None, TypeError), ("pps", "invalid", NeutralTradeAPIError), ("isLegacy", None, NeutralTradeAPIError)])
def test_page_progress_is_atomic_and_history_is_corrected(source: NeutralTradeDatabase, raw_vault: dict, invalid_field: str, invalid_value: object, error: type[Exception]) -> None:
    """Correct the same event while retaining resume state and unrelated history.

    An invalid page rolls back every inserted row and its cursor update.

    :param source: Locked archive fixture.
    :param raw_vault: Native directory configuration.
    :param invalid_field: Field rejected after the first row has been written.
    :param invalid_value: Malformed source value.
    :param error: Expected validation or conversion exception.
    """
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    assert not source.save_page(vault, "share-price", price_page("next"))
    broken = price_page("later")
    invalid = dict(broken["data"]["series"][0])
    invalid[invalid_field] = invalid_value
    broken["data"]["series"].append(invalid)
    with pytest.raises(error):
        source.save_page(vault, "share-price", broken)
    assert source.history_request(vault, "share-price")[0] == "next"
    assert source.con.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    source.record_error(vault, "share-price", "Invalid source page")
    assert source.save_page(vault, "share-price", price_page(None, "1200000"))
    frame = build_raw_prices_dataframe(source)
    assert len(frame) == 1
    assert frame.iloc[0]["share_price"] == pytest.approx(1.2)
    assert frame.iloc[0]["total_assets"] == pytest.approx(11000)
    assert source.con.execute("SELECT count(*) FROM duckdb_constraints() WHERE constraint_type IN ('PRIMARY KEY','UNIQUE')").fetchone()[0] == 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_onboarding_retry_preserves_existing_metadata(source: NeutralTradeDatabase, raw_vault: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dry_run: bool) -> None:
    """Retain earlier native decisions through failed onboarding and retry.

    The collector is replaced with an empty-history catalogue refresh; real
    metadata and Parquet merges must preserve existing history and stricter
    risk. A failed price merge must not commit metadata. Preview leaves all
    existing files byte-identical, and apply preserves unrelated price rows.

    :param source: Isolated archive used to seed an earlier verified observation.
    :param raw_vault: Public source configuration.
    :param tmp_path: Isolated migration files.
    :param monkeypatch: Replace network collection and inject one merge failure.
    :param dry_run: Exercise preview or persistent apply.
    :return: ``None`` after preservation and retry assertions.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    source.save_page(vault, "share-price", price_page())
    metadata_path = tmp_path / "vault-metadata-db.pickle"
    original = merge_into_vault_database(source, metadata_path)
    spec = VaultSpec(SOLANA_DATASET_CHAIN_ID, ADDRESS)
    earlier = datetime.datetime(2026, 8, 1)  # noqa: DTZ001 - dataset timestamps are naive UTC.
    original.rows[spec]["First seen"] = earlier
    original.rows[spec]["_risk"] = VaultTechnicalRisk.severe
    unrelated = VaultSpec(1, "0x" + "12" * 20)
    original.rows[unrelated] = {"Protocol": "Accountable", "manual": "preserve"}
    original.write(metadata_path)
    original_bytes = metadata_path.read_bytes()
    prices = build_raw_prices_dataframe(source)
    evm_prices = prices.assign(chain=1, address=unrelated.vault_address)
    prices = pd.concat([prices, evm_prices], ignore_index=True)
    prices[["management_fee", "performance_fee"]] = prices[["management_fee", "performance_fee"]].astype("float64")
    # Existing raw files use the shared millisecond timestamp schema.
    for column in ("timestamp", "written_at"):
        prices[column] = prices[column].dt.as_unit("ms")
    raw_path = tmp_path / "vault-prices-1h.parquet"
    prices.to_parquet(raw_path, index=False)
    raw_bytes = raw_path.read_bytes()

    def collect(db: NeutralTradeDatabase, _key: str, *, vault_ids: set[str], **_kwargs: object) -> ScanResult:
        """Supply catalogue metadata only, leaving existing verification useful."""
        for address in vault_ids:
            raw = copy.deepcopy(raw_vault)
            raw["bundleKey"] = address
            db.save_lead(parse_vault(raw))
        return ScanResult(leads=len(vault_ids))

    def fail_merge(**_kwargs: object) -> dict[str, bool]:
        """Model a failed price merge before a durable metadata commit."""
        message = "Injected price merge failure"
        raise ValueError(message)

    monkeypatch.setenv("NEUTRAL_TRADE_API_KEY", "synthetic-test-key")
    monkeypatch.setattr("eth_defi.neutral_trade.metrics.run_scan", collect)
    monkeypatch.setattr("eth_defi.vault.post_processing.merge_native_protocols", fail_merge)
    migration = next(item for item in runner.discover_migrations() if item.number == 66)
    with pytest.raises(ValueError, match="Injected"):
        runner._execute(migration, runner.MigrationContext(tmp_path, dry_run, script_name=migration.name))
    assert metadata_path.read_bytes() == original_bytes
    monkeypatch.setattr("eth_defi.vault.post_processing.merge_native_protocols", merge_native_protocols)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    result = context.metadata() if dry_run else VaultDatabase.read(metadata_path)
    assert result.rows[spec]["_risk"] == VaultTechnicalRisk.severe
    assert result.rows[spec]["First seen"] == earlier
    assert result.rows[spec]["_detection_data"].first_seen_at == earlier
    assert result.rows[spec]["_neutral_trade"]["history_verified"] is True
    assert result.rows[unrelated] == original.rows[unrelated]
    actual_prices = pd.read_parquet(raw_path)
    pd.testing.assert_frame_equal(actual_prices[prices.columns].sort_values("chain").reset_index(drop=True), prices.sort_values("chain").reset_index(drop=True), check_dtype=False)
    if dry_run:
        assert metadata_path.read_bytes() == original_bytes
        assert raw_path.read_bytes() == raw_bytes


@pytest.mark.parametrize("dry_run", [True, False])
def test_onboarding_refuses_uncheckpointed_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dry_run: bool) -> None:
    """Refuse both preview copies and apply backups when source WAL remains.

    Copying only the main DuckDB file would omit pending writes. This guard
    must run before any API reads or changes to the existing source files.

    :param tmp_path: Isolated migration state.
    :param monkeypatch: Supply a synthetic credential which must not be used.
    :param dry_run: Preview or apply copy path.
    :return: ``None`` after proving refusal preserves the original files.
    """
    monkeypatch.setenv("NEUTRAL_TRADE_API_KEY", "synthetic-test-key")
    VaultDatabase().write(tmp_path / "vault-metadata-db.pickle")
    path = tmp_path / "neutral-trade-vaults.duckdb"
    with NeutralTradeDatabase(path):
        pass
    wal = Path(str(path) + ".wal")
    wal.write_bytes(b"Uncheckpointed source sentinel")
    original = path.read_bytes()
    migration = next(item for item in runner.discover_migrations() if item.number == 66)
    with pytest.raises(AssertionError, match="Close/checkpoint"):
        runner._execute(migration, runner.MigrationContext(tmp_path, dry_run, script_name=migration.name))
    assert path.read_bytes() == original
    assert wal.read_bytes() == b"Uncheckpointed source sentinel"
    assert not list(tmp_path.glob("*.bak-*"))


def test_cursor_cycle_and_recovery(source: NeutralTradeDatabase, raw_vault: dict) -> None:
    """Reject a cursor revisited across several committed pages.

    Expired provider cursors restart the same fixed range without deleting data.

    :param source: Locked archive fixture.
    :param raw_vault: Native directory configuration.
    """
    vault = parse_vault(raw_vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    source.save_page(vault, "share-price", price_page("a"))
    source.save_page(vault, "share-price", price_page("b"))
    with pytest.raises(NeutralTradeAPIError, match="advance"):
        source.save_page(vault, "share-price", price_page("a"))
    source.record_error(vault, "share-price", "Neutral Trade HTTP 400")
    assert source.history_request(vault, "share-price")[0] is None
    assert source.con.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_exclusive_source_lock(source: NeutralTradeDatabase) -> None:
    """Reject a competing source writer instead of sharing a DuckDB handle.

    The original owner remains usable after acquisition is refused.

    :param source: Locked archive fixture.
    """
    with pytest.raises(Timeout):
        NeutralTradeDatabase(source.path)
    assert source.con.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize("unassessed_override", [False, True])
def test_identity_and_native_export(source: NeutralTradeDatabase, raw_vault: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, unassessed_override: bool) -> None:
    """Export canonical USDC data, preserve history and revoke public admission.

    The same merge path is used by the scheduler and manual post-processing.
    EVM records must not appear in shared metadata or prices.

    :param source: Locked archive fixture.
    :param raw_vault: Native directory configuration.
    :param tmp_path: Isolated shared outputs.
    :param monkeypatch: Install an optional address-specific unassessed risk.
    :param unassessed_override: Exercise the nullable risk-resolver contract.
    """
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    source.save_page(vault, "share-price", price_page())
    shared = tmp_path / "metadata.pickle"
    merged = merge_into_vault_database(source, shared)
    assert len(merged.rows) == 1
    spec = next(iter(merged.rows))
    assert spec.chain_id == SOLANA_DATASET_CHAIN_ID
    assert spec.vault_address == make_solana_vault_address(ADDRESS)
    assert merged.rows[spec]["_neutral_trade"]["vault_address"] == ADDRESS
    assert not merged.rows[spec]["_fees"].can_calculate_investor_net_performance()
    merged.rows[spec]["_risk"] = VaultTechnicalRisk.severe
    merged.write(shared)
    if unassessed_override:
        monkeypatch.setitem(VAULT_SPECIFIC_RISK, spec.vault_address, None)
    assert merge_into_vault_database(source, shared).rows[spec]["_risk"] == VaultTechnicalRisk.severe
    evm = copy.deepcopy(raw_vault)
    evm.update(bundleKey="0x" + "12" * 20, chain="evm:4663")
    evm["asset"]["mint"] = "0x" + "34" * 20
    source.save_lead(parse_vault(evm))
    assert len(merge_into_vault_database(source, shared).rows) == 1
    assert "Manually inspect" in render_verification_report(source)
    with pytest.raises(Timeout):
        merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=source.path, uncleaned_parquet_path=tmp_path / "raw.parquet")


def test_legacy_and_unknown_usd_never_become_live_prices(source: NeutralTradeDatabase, raw_vault: dict) -> None:
    """Keep fallback-bearing legacy data private and out of live return series.

    A one-dollar API valuation is not evidence of an actual market price.

    :param source: Locked archive fixture.
    :param raw_vault: Native directory configuration.
    """
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    source.save_page(vault, "share-price", price_page(legacy=True))
    assert build_raw_prices_dataframe(source).empty
    assert source.con.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_bulk_file_backed_duplicate_ingestion(source: NeutralTradeDatabase, raw_vault: dict) -> None:
    """Exercise unconstrained file-backed storage at a substantial row count.

    Source event corrections do not create ART indexes or duplicate logical keys.

    :param source: Locked archive fixture.
    :param raw_vault: Native directory configuration.
    """
    vault = parse_vault(raw_vault)
    source.con.execute("INSERT INTO observations SELECT 'solana',?, 'share-price', CAST(i AS VARCHAR), TIMESTAMP '2026-01-01', '{}','{}', current_timestamp FROM range(100000) AS r(i)", [ADDRESS])
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    source.save_page(vault, "share-price", price_page())
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12), refresh=True)
    source.save_page(vault, "share-price", price_page())
    assert source.con.execute("SELECT count(*) FROM observations").fetchone()[0] == 100001


@pytest.mark.live
def test_live_isolated_collection_and_json(tmp_path: Path) -> None:
    """Exercise the actual provider through storage, cleaning and combined JSON.

    A bounded recent range keeps this smoke test within three minutes. A second
    scan proves cursor/observation retention without touching production paths.

    :param tmp_path: Isolated source, metadata, Parquet and JSON outputs.
    """
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    if not key:
        if os.environ.get("REQUIRE_NEUTRAL_TRADE_LIVE_TESTS", "false").lower() == "true":
            pytest.fail("Partner key is required for real validation")
        pytest.skip("Partner key not supplied")
    end = native_datetime_utc_now().date()
    source_path = tmp_path / "source.duckdb"
    with NeutralTradeDatabase(source_path) as db:
        for _ in range(2):
            result = run_scan(db, key, vault_ids={ADDRESS}, start_date=end - datetime.timedelta(days=7), end_date=end, page_budget=10, time_budget=90)
            assert result.failures == 0 and not result.pending
        frame = build_raw_prices_dataframe(db)
        assert len(frame) > 1
        merge_into_vault_database(db, tmp_path / "vault-metadata-db.pickle")
    raw = tmp_path / "vault-prices-1h.parquet"
    cleaned = tmp_path / "cleaned-vault-prices-1h.parquet"
    merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=source_path, uncleaned_parquet_path=raw)
    generate_cleaned_vault_datasets(vault_db_path=tmp_path / "vault-metadata-db.pickle", price_df_path=raw, cleaned_price_df_path=cleaned, display=lambda _frame: None, logger=lambda _message: None)
    exported = export_json(data_dir=tmp_path, core3_db_path=tmp_path / "absent-core.duckdb", xerberus_db_path=tmp_path / "absent-risk.duckdb", feed_db_path=tmp_path / "absent-feed.duckdb")
    assert len(exported["vaults"]) == 1
    row = exported["vaults"][0]
    assert row["other_data"]["neutral_trade"]["vault_address"] == ADDRESS
    assert row["curator_name"] == "Xanthas"
    assert row["curator_slug"] == "xanthas"
    assert row["description"] and row["short_description"]
    assert exported["curators"]["xanthas"]["short_description"]
    assert exported["curators"]["xanthas"]["long_description"]
    assert row["fee_mode"] == "externalised"
    assert row["risk"] == "High"
    assert row["cagr_net"] is None
    assert row["three_months_sharpe_net"] is None


def test_stale_archive_and_newer_coverage_are_preserved(source: NeutralTradeDatabase, raw_vault: dict) -> None:
    """An outage and a later fixed-scope migration cannot purge or rewind history.

    Staleness is observability, not an explicit catalogue approval revocation.

    :param source: Locked archive fixture.
    :param raw_vault: Approved directory configuration.
    """
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 12, 1))
    source.save_page(vault, "share-price", price_page())
    source.con.execute("UPDATE leads SET last_seen=TIMESTAMP '2020-01-01'")
    source.con.execute("UPDATE history_sync SET last_success=TIMESTAMP '2020-01-01'")
    assert not build_raw_prices_dataframe(source).empty
    assert not source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
    assert source.history_request(vault, "share-price")[2] == datetime.date(2026, 12, 1)


def test_quarantine_preserves_raw_archive_and_suppresses_public_clean(tmp_path: Path, raw_vault: dict) -> None:
    """Withhold an unreviewed price reset without erasing an earlier raw series.

    Public cleaning writes an empty approved dataset when the sole source is
    explicitly quarantined; the original shared archival row remains intact.

    :param tmp_path: Isolated source/shared files.
    :param raw_vault: Public directory configuration.
    """
    path, metadata, prices, cleaned = (tmp_path / name for name in ("source.duckdb", "metadata.pickle", "raw.parquet", "cleaned.parquet"))
    vault = parse_vault(raw_vault)
    with NeutralTradeDatabase(path) as db:
        db.save_lead(vault)
        db.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
        db.save_page(vault, "share-price", price_page())
        merge_into_vault_database(db, metadata)
    merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=path, uncleaned_parquet_path=prices)
    with NeutralTradeDatabase(path) as db:
        db.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12), refresh=True)
        changed = price_page(None, "200000")
        changed["data"]["series"][0]["blockTime"] += 86400
        changed["data"]["series"][0]["slot"] = 2
        db.save_page(vault, "share-price", changed)
        assert build_raw_prices_dataframe(db).empty
        merge_into_vault_database(db, metadata)
    merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=path, uncleaned_parquet_path=prices)
    assert pq.read_table(prices).num_rows == 1
    generate_cleaned_vault_datasets(vault_db_path=metadata, price_df_path=prices, cleaned_price_df_path=cleaned, display=lambda _frame: None, logger=lambda _message: None)
    assert pq.read_table(cleaned).num_rows == 0


def test_hidden_native_does_not_disable_generic_empty_guard(tmp_path: Path, raw_vault: dict) -> None:
    """Keep the last public dataset safe when an unrelated source is empty.

    A revoked Neutral Trade row must not globally permit zero-row cleaning for
    a multichain catalogue with other approved protocols.

    :param tmp_path: Isolated test storage.
    :param raw_vault: Public native configuration used to construct metadata.
    """
    path, metadata, prices, cleaned = (tmp_path / name for name in ("source.duckdb", "metadata.pickle", "raw.parquet", "cleaned.parquet"))
    vault = parse_vault(raw_vault)
    with NeutralTradeDatabase(path) as db:
        db.save_lead(vault)
        db.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 11, 12))
        db.save_page(vault, "share-price", price_page())
        shared = merge_into_vault_database(db, metadata)
    merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=path, uncleaned_parquet_path=prices)
    native_spec = next(iter(shared.rows))
    unrelated = copy.deepcopy(shared.rows[native_spec])
    unrelated.update(Protocol="ApeX", Address="apex-vault-fixture")
    unrelated.pop("_neutral_trade")
    unrelated["_detection_data"] = replace(unrelated["_detection_data"], chain=9995, address="apex-vault-fixture")
    shared.rows[VaultSpec(9995, "apex-vault-fixture")] = unrelated
    shared.rows[native_spec]["_neutral_trade"]["visible"] = False
    shared.write(metadata)
    with pytest.raises(ValueError, match="zero rows"):
        generate_cleaned_vault_datasets(vault_db_path=metadata, price_df_path=prices, cleaned_price_df_path=cleaned, display=lambda _frame: None, logger=lambda _message: None)
    assert not cleaned.exists()


def test_manual_summary_uses_shared_metrics_and_suppresses_unclear_basis(source: NeutralTradeDatabase, raw_vault: dict) -> None:
    """Calculate canonical lifetime metrics and withhold unexplained repricing.

    A stable-equity PPS jump is a review flag, not an automatically repaired
    price or a fabricated investment return.

    :param source: Locked archive fixture.
    :param raw_vault: Public USDC directory record.
    """
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    end = datetime.date(2026, 9, 30)
    source.prepare_history(vault, "share-price", start_date=None, end_date=end)
    rows = []
    start = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    pps = 1_000_000
    for day in range(61):
        if day:
            pps = int(pps * (1.002 if day % 2 else 0.9995))
        rows.append({"blockTime": int((start + datetime.timedelta(days=day)).timestamp()), "pps": str(pps), "totalEquity": str(pps * 10000), "totalShares": "10000000000", "isLegacy": False, "signature": f"fixture-{day}", "slot": day + 1, "sourceEvent": "OracleUpdated"})
    source.save_page(vault, "share-price", {"data": {"series": rows, "nextCursor": None}})
    source.prepare_history(vault, "snapshots", start_date=None, end_date=end)
    source.save_page(vault, "snapshots", {"data": {"snapshots": [], "nextCursor": None}})
    as_of = (start + datetime.timedelta(days=60)).replace(tzinfo=None)
    frame = build_performance_summary(source, as_of=as_of)
    assert frame.iloc[0]["age_days"] == 60
    expected_cagr = (pps / 1_000_000) ** (365.25 / 60) - 1
    assert frame.iloc[0]["cagr"] == pytest.approx(expected_cagr)
    assert pd.notna(frame.iloc[0]["sharpe"])
    source.prepare_history(vault, "share-price", start_date=None, end_date=end, refresh=True)
    repriced = dict(rows[-1])
    repriced["pps"] = str(int(pps * 1.1))
    source.save_page(vault, "share-price", {"data": {"series": [repriced], "nextCursor": None}})
    flagged = build_performance_summary(source, as_of=as_of)
    assert pd.isna(flagged.iloc[0]["cagr"])
    assert pd.isna(flagged.iloc[0]["sharpe"])
    assert flagged.iloc[0]["status"].startswith("unverified:")


@pytest.mark.parametrize("zero_fees", [False, True])
def test_base_schedule_flows_to_net_json(source: NeutralTradeDatabase, raw_vault: dict, tmp_path: Path, *, zero_fees: bool) -> None:
    """Export externalised or explicitly feeless schedules through real analytics.

    Sixty days of synthetic observations allow gross/net comparison without
    depending on the provider's history or treating missing rates as zero.

    :param source: Isolated file-backed source archive.
    :param raw_vault: Native directory configuration.
    :param tmp_path: Isolated metadata, Parquet and JSON outputs.
    :param zero_fees: Whether all four published base rates are explicitly zero.
    :return: ``None`` after checking exported risk, fees and net estimates.
    """
    raw_vault.update(depositFeeBps=0, withdrawalFeeBps=30)
    if zero_fees:
        raw_vault.update(managementFeeBps=0, performanceFeeBps=0, withdrawalFeeBps=0)
    vault = parse_vault(raw_vault)
    source.save_lead(vault)
    source.prepare_history(vault, "share-price", start_date=None, end_date=datetime.date(2026, 10, 1))
    start = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    rows = [{"blockTime": int((start + datetime.timedelta(days=day)).timestamp()), "pps": str(1_000_000 + day * 1000 + day % 2 * 100), "totalEquity": str((1_000_000 + day * 1000 + day % 2 * 100) * 10000), "totalShares": "10000000000", "isLegacy": False, "signature": str(day)} for day in range(61)]
    source.save_page(vault, "share-price", {"data": {"series": rows, "nextCursor": None}})
    metadata = tmp_path / "vault-metadata-db.pickle"
    database = merge_into_vault_database(source, metadata)
    fees = next(iter(database.rows.values()))["_fees"]
    assert fees.fee_mode == (VaultFeeMode.feeless if zero_fees else VaultFeeMode.externalised)
    assert fees.can_calculate_investor_net_performance()
    assert fees.get_net_fees().management == fees.management
    build_raw_prices_dataframe(source).to_parquet(tmp_path / "vault-prices-1h.parquet", index=False)
    generate_cleaned_vault_datasets(vault_db_path=metadata, price_df_path=tmp_path / "vault-prices-1h.parquet", cleaned_price_df_path=tmp_path / "cleaned-vault-prices-1h.parquet", display=lambda _frame: None, logger=lambda _message: None)
    exported = export_json(data_dir=tmp_path, core3_db_path=tmp_path / "absent-core.duckdb", xerberus_db_path=tmp_path / "absent-risk.duckdb", feed_db_path=tmp_path / "absent-feed.duckdb")
    row = exported["vaults"][0]
    assert row["fee_mode"] == fees.fee_mode.value
    assert row["risk"] == "High"
    assert row["risk_numeric"] == 30
    assert row["cagr_net"] is not None
    if zero_fees:
        assert row["cagr_net"] == pytest.approx(row["cagr"])
    else:
        assert row["cagr_net"] < row["cagr"]
        assert row["management_fee"] == pytest.approx(0.01)
        assert row["performance_fee"] == pytest.approx(0.2)
