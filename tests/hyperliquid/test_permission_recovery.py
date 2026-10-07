"""Permission receipt, recovery and unknown-state regression coverage."""

# ruff: noqa: PLR2004 - Exact values belong to the fixed recovery fixtures.

import datetime
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace

import duckdb
import pandas as pd
import pytest

import eth_defi.hyperliquid.permission_recovery as recovery
from eth_defi.hyperliquid import daily_metrics, high_freq_metrics
from eth_defi.hyperliquid.high_freq_metrics import HyperliquidHighFreqMetricsDatabase, HyperliquidHighFreqPriceRow, fetch_and_store_vault_high_freq
from eth_defi.hyperliquid.permission import PermissionObservation, append_permission_observation, select_permission_state
from eth_defi.hyperliquid.permission_recovery import file_sha256, recover_permissions
from eth_defi.hyperliquid.vault import HyperliquidVault

ADDRESS = "0x" + "a" * 40
T0 = datetime.datetime(2026, 9, 21, 10)  # noqa: DTZ001 - Naive UTC, as required for source clocks.


def test_permission_unknown_and_bucket_clock() -> None:
    """A missing flag pair clears prior openness at its rounded receipt time.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :return: ``None`` after validating the expected behaviour.
    """
    observed = [PermissionObservation("open", ADDRESS, permission_observed_at=T0, is_closed=False, allow_deposits=True, provenance="observed"), PermissionObservation("unknown", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=1), provenance="observed_unknown")]
    decisions = pd.DataFrame({"vault_address": [ADDRESS] * 3, "timestamp": [T0, T0 + datetime.timedelta(hours=2), T0 + datetime.timedelta(hours=3)]})
    state = select_permission_state(pd.DataFrame([asdict(row) for row in observed]), decisions, frequency="2h")
    assert state.iloc[0].allow_deposits
    assert pd.isna(state.iloc[1].allow_deposits)
    assert state.iloc[1].provenance == "observed_unknown"


@pytest.mark.parametrize("decision_unit,source_unit", [("ns", "us"), ("us", "ns"), ("ms", "us")])
def test_permission_selector_accepts_sidecar_and_grid_datetime_units(decision_unit: str, source_unit: str) -> None:
    """Arrow receipt precision can differ from a Pandas decision grid's unit.

    Exercise both archive fallback and real-receipt joins, including bucket
    rounding without changing the exact subsecond source receipt clock.
    :param decision_unit: Precision of the decision-grid timestamps.
    :param source_unit: Precision of the source observation timestamps.
    :return: ``None`` after validating the expected behaviour.
    """
    receipt = T0 + datetime.timedelta(hours=4, seconds=5, microseconds=123)
    rows = [
        PermissionObservation("gap", ADDRESS, record_kind="uncertainty_boundary", effective_from=T0, provenance="corrupted_unknown"),
        PermissionObservation("archive", ADDRESS, evidence_available_at=T0 + datetime.timedelta(hours=2), allow_deposits=False, provenance="legacy_closure_bounded"),
        PermissionObservation("real", ADDRESS, permission_observed_at=receipt, is_closed=False, allow_deposits=True, provenance="observed"),
    ]
    observations = pd.DataFrame([asdict(row) for row in rows])
    for name in ("permission_observed_at", "evidence_available_at", "effective_from", "effective_to", "capacity_observed_at"):
        observations[name] = pd.to_datetime(observations[name]).astype(f"datetime64[{source_unit}]")
    decisions = pd.DataFrame({"vault_address": [ADDRESS] * 3, "timestamp": [T0 + datetime.timedelta(hours=hour) for hour in (1, 3, 5)]})
    decisions["timestamp"] = decisions["timestamp"].astype(f"datetime64[{decision_unit}]")
    states = select_permission_state(observations, decisions, frequency="1h")
    assert states.provenance.tolist() == ["corrupted_unknown", "legacy_closure_bounded", "observed"]
    assert pd.isna(states.iloc[0].allow_deposits)
    assert not states.iloc[1].allow_deposits
    assert states.iloc[2].allow_deposits
    assert states.iloc[2].permission_observed_at == receipt


def test_uncertainty_interval_needs_real_later_receipt() -> None:
    """A historical gap overrides prior open without using publication time.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [PermissionObservation("open", ADDRESS, permission_observed_at=T0, is_closed=False, allow_deposits=True, provenance="observed"), PermissionObservation("gap", ADDRESS, record_kind="uncertainty_boundary", effective_from=T0 + datetime.timedelta(hours=1), written_at=T0 + datetime.timedelta(days=30), provenance="corrupted_unknown"), PermissionObservation("new", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=3), is_closed=False, allow_deposits=True, provenance="observed")]
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), pd.DataFrame({"vault_address": [ADDRESS] * 3, "timestamp": [T0, T0 + datetime.timedelta(hours=2), T0 + datetime.timedelta(hours=4)]}))
    assert state.provenance.tolist() == ["observed", "corrupted_unknown", "observed"]


def test_price_overlap_preserves_write_clock(tmp_path: Path) -> None:
    """Repeated portfolio prices cannot refresh permission or original write age.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "hf.duckdb")
    try:
        row = HyperliquidHighFreqPriceRow(vault_address=ADDRESS, timestamp=T0, share_price=1, tvl=10, cumulative_pnl=0, written_at=T0, is_closed=False, allow_deposits=True)
        db.upsert_high_freq_prices([row, row])
        row.written_at = T0 + datetime.timedelta(days=1)
        row.is_closed = None
        row.allow_deposits = None
        row.share_price = 2
        db.upsert_high_freq_prices([row])
        prices = db.get_all_high_freq_prices()
        assert len(prices) == 1
        assert prices.iloc[0].written_at == T0
        assert prices.iloc[0].share_price == 2
    finally:
        db.close()


def make_legacy(path: Path, rows: list[tuple]) -> None:
    """Create an old constrained file with sparse permission evidence."""
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE TABLE vault_high_freq_prices (vault_address VARCHAR, timestamp TIMESTAMP, share_price DOUBLE, tvl DOUBLE, is_closed BOOLEAN, allow_deposits BOOLEAN, leader_fraction DOUBLE, written_at TIMESTAMP, PRIMARY KEY(vault_address,timestamp))")
        connection.executemany("INSERT INTO vault_high_freq_prices VALUES (?,?,?,?,?,?,?,?)", rows)
    finally:
        connection.close()


def test_recovery_own_backup_dry_run_conflict_and_idempotence(tmp_path: Path) -> None:
    """Dry run is byte-preserving; apply restores missing keys and audits conflicts.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    current, old = tmp_path / "current.duckdb", tmp_path / "old.duckdb"
    make_legacy(current, [(ADDRESS, T0, 2, 10, False, True, 0.1, T0)])
    make_legacy(old, [(ADDRESS, T0, 1, 10, False, True, 0.1, T0), (ADDRESS, T0 + datetime.timedelta(hours=1), 1, 10, True, False, 0.1, T0)])
    digest = file_sha256(current)
    report = recover_permissions(current, [old], tmp_path / "backups")
    assert report["tables"]["vault_high_freq_prices"]["restore_missing"] == 1
    assert file_sha256(current) == digest
    assert not (tmp_path / "backups").exists()
    report = recover_permissions(current, [old], tmp_path / "backups", dry_run=False)
    assert Path(report["backup"]["backup"]).exists()
    assert file_sha256(Path(report["backup"]["backup"])) == digest
    connection = duckdb.connect(str(current), read_only=True)
    try:
        rows = connection.execute("SELECT share_price,is_closed,allow_deposits FROM vault_high_freq_prices ORDER BY timestamp").fetchall()
        assert rows == [(2, None, None), (1, None, None)]
        assert connection.execute("SELECT count(*) FROM duckdb_constraints() WHERE constraint_type='PRIMARY KEY'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM hypercore_price_conflicts").fetchone()[0] == 1
        evidence = connection.execute("SELECT count(*) FROM hypercore_legacy_price_evidence").fetchone()[0]
    finally:
        connection.close()
    report = recover_permissions(current, [old], tmp_path / "backups", dry_run=False)
    assert report["tables"]["vault_high_freq_prices"]["restore_missing"] == 0
    assert report["legacy_evidence_rows"] == evidence


def test_recovered_backup_flags_use_price_clock_and_leave_unrecoverable_unknown(tmp_path: Path) -> None:
    """Late corrupted writes cannot date recovered permissions in October.

    A matching price key retains its current economics and write clock, whilst
    trusted backup flags supply an inferred September permission clock. A key
    with no recoverable backup flags supplies no fabricated observation.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    current, old = tmp_path / "current.duckdb", tmp_path / "old.duckdb"
    late = T0 + datetime.timedelta(days=14)
    make_legacy(current, [(ADDRESS, T0, 2, 10, False, True, 0.1, late)])
    make_legacy(old, [(ADDRESS, T0, 1, 10, False, False, 0.1, late), (ADDRESS, T0 + datetime.timedelta(hours=1), 1, 10, None, None, None, late)])
    digest = file_sha256(current)
    dry = recover_permissions(current, [old], tmp_path / "backups")
    assert dry["legacy_price_timestamp_permissions"] == 1
    assert file_sha256(current) == digest
    recover_permissions(current, [old], tmp_path / "backups", dry_run=False)
    connection = duckdb.connect(str(current), read_only=True)
    try:
        assert connection.execute("SELECT share_price,written_at FROM vault_high_freq_prices WHERE timestamp=?", [T0]).fetchone() == (2, late)
        snapshot = connection.execute("SELECT permission_observed_at,allow_deposits,capacity_observed_at,leader_fraction FROM vault_permission_observations WHERE provenance='legacy_price_timestamp'").fetchone()
        assert snapshot == (T0, False, None, 0.1)
        before = connection.execute("SELECT * FROM vault_permission_observations ORDER BY observation_id").fetchall()
    finally:
        connection.close()
    recover_permissions(current, [old], tmp_path / "backups", dry_run=False)
    connection = duckdb.connect(str(current), read_only=True)
    try:
        assert connection.execute("SELECT * FROM vault_permission_observations ORDER BY observation_id").fetchall() == before
    finally:
        connection.close()


def test_inferred_price_clock_recovers_open_but_never_overrides_genuine_unknown() -> None:
    """Recovered flags are usable at their bucket, with genuine receipts first.

    Explicit unknown API responses supersede inferred flags even when an
    inferred price timestamp is later. Capacity freshness is never inferred.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [
        PermissionObservation("gap", ADDRESS, record_kind="uncertainty_boundary", effective_from=T0, provenance="corrupted_unknown"),
        PermissionObservation("fallback", ADDRESS, permission_observed_at=T0, written_at=T0 + datetime.timedelta(days=14), is_closed=False, allow_deposits=True, provenance="legacy_price_timestamp"),
        PermissionObservation("unknown", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=1), provenance="observed_unknown"),
        PermissionObservation("later-fallback", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=2), is_closed=False, allow_deposits=True, provenance="legacy_price_timestamp"),
    ]
    decisions = pd.DataFrame({"vault_address": [ADDRESS] * 3, "timestamp": [T0 + datetime.timedelta(hours=h) for h in (0, 1, 3)]})
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), decisions, frequency="1h")
    assert state.provenance.tolist() == ["legacy_price_timestamp", "observed_unknown", "observed_unknown"]
    assert state.iloc[0].allow_deposits
    assert state.iloc[0].permission_observed_at == T0
    assert state.iloc[0].capacity_observed_at is pd.NaT or pd.isna(state.iloc[0].capacity_observed_at)
    assert pd.isna(state.iloc[2].allow_deposits)


def test_inferred_price_clock_rounds_up_and_cannot_bridge_an_unrecovered_gap() -> None:
    """An inferred flag becomes available at the next bucket, within its gap.

    Clock rounding must happen before snapshot selection. A flag preceding
    an explicit uncertainty boundary cannot authenticate that later interval.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [
        PermissionObservation("fallback", ADDRESS, permission_observed_at=T0 + datetime.timedelta(minutes=1), is_closed=False, allow_deposits=True, provenance="legacy_price_timestamp"),
        PermissionObservation("gap", ADDRESS, record_kind="uncertainty_boundary", effective_from=T0 + datetime.timedelta(hours=2), provenance="corrupted_unknown"),
    ]
    decisions = pd.DataFrame({"vault_address": [ADDRESS] * 3, "timestamp": [T0 + datetime.timedelta(hours=h) for h in (0, 1, 2)]})
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), decisions, frequency="1h")
    assert pd.isna(state.iloc[0].allow_deposits)
    assert state.iloc[1].allow_deposits
    assert state.iloc[2].provenance == "corrupted_unknown"
    assert pd.isna(state.iloc[2].allow_deposits)


def test_inferred_hf_flag_takes_precedence_over_daily_at_same_price_clock() -> None:
    """Daily compatibility flags cannot override finer same-clock evidence.

    Both archived rows remain auditable. Their selected state follows the
    same source preference as the combined price export.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [
        PermissionObservation("daily", ADDRESS, permission_observed_at=T0, is_closed=False, allow_deposits=True, provenance="legacy_price_timestamp", source_endpoint="archive price rows/vault_daily_prices"),
        PermissionObservation("hf", ADDRESS, permission_observed_at=T0, is_closed=False, allow_deposits=False, provenance="legacy_price_timestamp", source_endpoint="archive price rows/vault_high_freq_prices"),
    ]
    decisions = pd.DataFrame({"vault_address": [ADDRESS], "timestamp": [T0]})
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), decisions)
    assert state.iloc[0].observation_id == "hf"
    assert not state.iloc[0].allow_deposits


def test_inferred_permission_expires_from_original_clock_not_rounded_bucket() -> None:
    """Two-day fallback carry-forward cannot keep unrecovered rows open forever.

    The exact original clock defines age even when bucket rounding moves its
    apparent availability. A genuine response has separate consumer ageing.
    :return: ``None`` after validating the expected behaviour.
    """
    clock = T0 + datetime.timedelta(minutes=1)
    rows = [PermissionObservation("fallback", ADDRESS, permission_observed_at=clock, is_closed=False, allow_deposits=True, provenance="legacy_price_timestamp")]
    decisions = pd.DataFrame({"vault_address": [ADDRESS] * 2, "timestamp": [T0 + datetime.timedelta(days=2), T0 + datetime.timedelta(days=2, hours=1)]})
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), decisions, frequency="1h")
    assert state.iloc[0].allow_deposits
    assert pd.isna(state.iloc[1].allow_deposits)


def test_raw_carried_flags_do_not_redate_scanner_permission(tmp_path: Path) -> None:
    """Parquet cannot turn NULL scanner rows into newer inferred measurements.

    Scanner archives define the original sparse snapshots. Raw-only keys
    still support explicitly inferred recovery when no scanner key exists.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    current, first, second = tmp_path / "current.duckdb", tmp_path / "first.duckdb", tmp_path / "second.duckdb"
    t1 = T0 + datetime.timedelta(days=3)
    t2 = t1 + datetime.timedelta(hours=1)
    make_legacy(current, [(ADDRESS, T0, 2, 10, False, True, 0.1, T0)])
    make_legacy(first, [(ADDRESS, T0, 1, 10, False, False, None, T0), (ADDRESS, t1, 1, 10, None, None, None, t1)])
    make_legacy(second, [(ADDRESS, T0, 1, 10, False, True, None, T0), (ADDRESS, t1, 1, 10, None, None, None, t1)])
    raw = tmp_path / "vault-prices-1h.parquet"
    pd.DataFrame({"chain": [9999] * 3, "address": [ADDRESS] * 3, "timestamp": [T0, t1, t2], "share_price": [1.0] * 3, "total_assets": [10.0] * 3, "hypercore_source": ["hf"] * 3, "deposits_open": ["true"] * 3}).to_parquet(raw, index=False)
    # First repair has only the raw source. A better scanner source on rerun
    # must retract its older raw-derived annotation at the NULL scanner key.
    recover_permissions(current, [], tmp_path / "backups", dry_run=False, parquet_sources=[raw])
    recover_permissions(current, [first, second], tmp_path / "backups", dry_run=False, parquet_sources=[raw])
    connection = duckdb.connect(str(current), read_only=True)
    try:
        observations = connection.execute("SELECT * FROM vault_permission_observations").df()
        assert connection.execute("SELECT permission_observed_at,allow_deposits FROM vault_permission_observations WHERE provenance='legacy_price_timestamp' ORDER BY permission_observed_at").fetchall() == [(T0, False), (t2, True)]
    finally:
        connection.close()
    state = select_permission_state(observations, pd.DataFrame({"vault_address": [ADDRESS], "timestamp": [t1]}))
    assert pd.isna(state.iloc[0].allow_deposits)


def test_repaired_backup_retains_its_inferred_truth_on_a_new_target(tmp_path: Path) -> None:
    """NULL compatibility fields in a repaired backup do not discard its evidence.

    Canonical annotation replacement must import the backup's independent
    legacy records as well as its prices, preserving the original clock.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    old, repaired, target = tmp_path / "old.duckdb", tmp_path / "repaired.duckdb", tmp_path / "target.duckdb"
    row = (ADDRESS, T0, 1, 10, False, False, None, T0)
    make_legacy(old, [row])
    make_legacy(repaired, [row])
    recover_permissions(repaired, [old], tmp_path / "backups", dry_run=False)
    make_legacy(target, [(ADDRESS, T0, 2, 10, False, True, None, T0)])
    recover_permissions(target, [repaired], tmp_path / "backups", dry_run=False)
    connection = duckdb.connect(str(target), read_only=True)
    try:
        assert connection.execute("SELECT permission_observed_at,allow_deposits FROM vault_permission_observations WHERE provenance='legacy_price_timestamp'").fetchall() == [(T0, False)]
    finally:
        connection.close()


def test_duplicate_scanner_archive_is_rejected_before_own_backup(tmp_path: Path) -> None:
    """Unconstrained archives must not introduce ambiguous permission truth.

    Reject duplicate keys during read-only validation, rather than depending
    on row order or introducing an ART uniqueness index.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    old, target = tmp_path / "old.duckdb", tmp_path / "target.duckdb"
    row = (ADDRESS, T0, 1, 10, False, False, None, T0)
    make_legacy(old, [row])
    make_legacy(target, [row])
    connection = duckdb.connect(str(old))
    try:
        connection.execute("CREATE TABLE unconstrained AS SELECT * FROM vault_high_freq_prices")
        connection.execute("DROP TABLE vault_high_freq_prices")
        connection.execute("ALTER TABLE unconstrained RENAME TO vault_high_freq_prices")
        connection.execute("INSERT INTO vault_high_freq_prices SELECT * FROM vault_high_freq_prices")
    finally:
        connection.close()
    digest = file_sha256(target)
    with pytest.raises(ValueError, match="Scanner archive contains duplicate"):
        recover_permissions(target, [old], tmp_path / "backups", dry_run=False)
    assert file_sha256(target) == digest
    assert not (tmp_path / "backups").exists()


def test_backup_failure_aborts_before_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A backup failure leaves the old database untouched.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :param monkeypatch: Replace transports or inject a controlled failure.
    :return: ``None`` after validating the expected behaviour.
    """
    current = tmp_path / "current.duckdb"
    make_legacy(current, [(ADDRESS, T0, 2, 10, False, True, 0.1, T0)])

    def fail_backup(*_args: object, **_kwargs: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(recovery, "backup_database", fail_backup)
    digest = file_sha256(current)
    with pytest.raises(OSError, match="disk full"):
        recover_permissions(current, [], tmp_path / "backups", dry_run=False)
    assert file_sha256(current) == digest


def test_same_bucket_uses_last_receipt_and_exact_conflict_is_unknown() -> None:
    """Bucket rounding preserves actual response order and exact ties stay unknown.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [PermissionObservation("z-first", ADDRESS, permission_observed_at=T0 + datetime.timedelta(minutes=1), is_closed=False, allow_deposits=True, provenance="observed"), PermissionObservation("a-last", ADDRESS, permission_observed_at=T0 + datetime.timedelta(minutes=2), is_closed=True, allow_deposits=False, provenance="observed")]
    decisions = pd.DataFrame({"vault_address": [ADDRESS], "timestamp": [T0 + datetime.timedelta(hours=2)]})
    frame = pd.DataFrame([asdict(row) for row in rows])
    state = select_permission_state(frame, decisions, frequency="2h")
    assert state.iloc[0].observation_id == "a-last"
    assert not state.iloc[0].allow_deposits
    rows[0].permission_observed_at = rows[1].permission_observed_at
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), decisions, frequency="2h")
    assert pd.isna(state.iloc[0].allow_deposits)
    assert state.iloc[0].provenance == "observed_unknown"


def test_raw_parquet_restores_prices_with_explicit_inferred_permission_clock(tmp_path: Path) -> None:
    """A raw R2 archive supplies classified flags with an inferred price clock.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    current = tmp_path / "current.duckdb"
    make_legacy(current, [(ADDRESS, T0, 2, 10, False, True, 0.1, T0)])
    raw = tmp_path / "vault-prices-1h.parquet"
    pd.DataFrame({"chain": [9999], "address": [ADDRESS], "timestamp": [T0 + datetime.timedelta(hours=1)], "share_price": [3.0], "total_assets": [20.0], "hypercore_source": ["hf"], "deposits_open": ["true"], "written_at": [T0]}).to_parquet(raw, index=False)
    report = recover_permissions(current, [], tmp_path / "backups", parquet_sources=[raw])
    assert report["tables"]["vault_high_freq_prices"]["restore_missing"] == 1
    report = recover_permissions(current, [], tmp_path / "backups", dry_run=False, parquet_sources=[raw])
    assert report["tables"]["vault_high_freq_prices"]["rows_after"] == 2
    connection = duckdb.connect(str(current), read_only=True)
    try:
        assert connection.execute("SELECT share_price,is_closed FROM vault_high_freq_prices ORDER BY timestamp DESC LIMIT 1").fetchone() == (3, None)
        assert connection.execute("SELECT count(*) FROM hypercore_raw_parquet_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT permission_observed_at,allow_deposits,provenance FROM vault_permission_observations WHERE provenance='legacy_price_timestamp'").fetchone() == (T0 + datetime.timedelta(hours=1), True, "legacy_price_timestamp")
    finally:
        connection.close()


def test_collector_records_permissions_without_portfolio_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful response changes permissions even when prices are unavailable.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :param monkeypatch: Replace transports or inject a controlled failure.
    :return: ``None`` after validating the expected behaviour.
    """

    info = SimpleNamespace(is_closed=True, allow_deposits=False, relationship_type="normal", leader_fraction=None, portfolio={})
    monkeypatch.setattr(HyperliquidVault, "fetch_metadata", lambda _self: info)
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "no-prices.duckdb")
    try:
        summary = SimpleNamespace(vault_address=ADDRESS, name="closed")
        assert not fetch_and_store_vault_high_freq(SimpleNamespace(), db, summary, flow_backfill_days=0)
        observations = db.get_permission_observations()
        assert len(observations) == 1
        assert observations.iloc[0].is_closed
        assert pd.notna(observations.iloc[0].permission_observed_at)
        assert db.get_all_high_freq_prices().empty
    finally:
        db.close()


def test_collector_refuses_art_schema_before_initialisation(tmp_path: Path) -> None:
    """Legacy schema refusal cannot mutate the database before its own backup.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "legacy.duckdb"
    make_legacy(path, [(ADDRESS, T0, 2, 10, True, False, 0.1, T0)])
    digest = file_sha256(path)
    with pytest.raises(RuntimeError, match="migration before starting"):
        HyperliquidHighFreqMetricsDatabase(path)
    assert file_sha256(path) == digest


def test_lower_priority_missing_key_conflicts_and_target_closure_repeat(tmp_path: Path) -> None:
    """Alternatives for absent live keys are audited and deny evidence is stable.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    current, first, second = tmp_path / "current.duckdb", tmp_path / "first.duckdb", tmp_path / "second.duckdb"
    make_legacy(current, [(ADDRESS, T0, 2, 10, True, False, 0.1, T0)])
    later = T0 + datetime.timedelta(hours=1)
    make_legacy(first, [(ADDRESS, later, 1, 10, None, None, 0.1, T0)])
    make_legacy(second, [(ADDRESS, later, 3, 10, None, None, 0.2, T0)])
    first_report = recover_permissions(current, [first, second], tmp_path / "backups", dry_run=False)
    connection = duckdb.connect(str(current), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM hypercore_price_conflicts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM hypercore_legacy_price_evidence WHERE is_closed IS NULL AND leader_fraction IS NOT NULL").fetchone()[0] == 2
    finally:
        connection.close()
    second_report = recover_permissions(current, [first, second], tmp_path / "backups", dry_run=False)
    assert second_report["permission_records"] == first_report["permission_records"]


def test_archive_closure_does_not_override_genuine_later_receipt() -> None:
    """Archive availability is not an ordering clock for current source truth.

    Exercise coherent source evidence and the original-clock recovery policy;
    check the resulting state and retained database contents.
    :return: ``None`` after validating the expected behaviour.
    """
    rows = [PermissionObservation("real", ADDRESS, permission_observed_at=T0, is_closed=False, allow_deposits=True, provenance="observed"), PermissionObservation("archive", ADDRESS, evidence_available_at=T0 + datetime.timedelta(hours=1), is_closed=True, allow_deposits=False, provenance="legacy_closure_bounded"), PermissionObservation("gap", ADDRESS, record_kind="uncertainty_boundary", effective_from=T0 - datetime.timedelta(days=1), provenance="corrupted_unknown")]
    state = select_permission_state(pd.DataFrame([asdict(row) for row in rows]), pd.DataFrame({"vault_address": [ADDRESS], "timestamp": [T0 + datetime.timedelta(hours=2)]}))
    assert state.iloc[0].observation_id == "real"
    assert state.iloc[0].allow_deposits


@pytest.mark.parametrize("allow_deposits", [False, True])
def test_collector_retains_permission_when_portfolio_parsing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, allow_deposits: bool) -> None:
    """A successful response remains evidence when unrelated portfolio parsing fails.

    The shared collector path records both the original flags and the parse error,
    so a broken portfolio cannot silently retain an earlier open state.

    :param tmp_path: Isolated file-backed database directory.
    :param monkeypatch: Replace provider transport with a fixed response receipt.
    :param allow_deposits: Closed permission or an open low-share policy block.
    :return: ``None`` after checking recorded policy inputs and failure type.
    """

    def fetch_malformed_metadata(vault: HyperliquidVault) -> None:
        """Retain the successful receipt before rejecting malformed price history.

        This simulates the API envelope captured before portfolio parsing.

        :param vault: Client being exercised by the shared fetch path.
        :return: Never returns; raises the fixture's parsing error.
        """
        vault.permission_received_at = T0
        vault.permission_payload = {"isClosed": False, "allowDeposits": allow_deposits, "leaderFraction": 0.04, "relationship": {"type": "normal"}}
        raise ValueError("Malformed portfolio")

    monkeypatch.setattr(HyperliquidVault, "fetch_metadata", fetch_malformed_metadata)
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "malformed-portfolio.duckdb")
    try:
        summary = SimpleNamespace(vault_address=ADDRESS, name="malformed")
        assert not fetch_and_store_vault_high_freq(SimpleNamespace(), db, summary, flow_backfill_days=0)
        observation = db.get_permission_observations().iloc[0]
        assert observation.permission_observed_at == T0
        assert observation.allow_deposits == allow_deposits
        assert observation.leader_fraction == pytest.approx(0.04)
        assert observation.max_deposit == 0.0 if allow_deposits else pd.isna(observation.max_deposit)
        assert db.con.execute("SELECT error_type FROM vault_permission_errors").fetchone()[0] == "ValueError"
        assert db.get_all_high_freq_prices().empty
    finally:
        db.close()


def test_bulk_closure_deduplication_and_unknown_reset(tmp_path: Path) -> None:
    """Bulk closures survive catalogue filters without appending identical denials.

    A bulk open flag cannot certify deposits. Identical closed responses retain
    the first clock; a later genuine Unknown permits recording closure again.

    :param tmp_path: Isolated database directory.
    :return: ``None`` after checking count, coherence and retained clocks.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "bulk.duckdb")
    try:
        closed = SimpleNamespace(vault_address=ADDRESS, is_closed=True)
        opened = SimpleNamespace(vault_address="0x" + "b" * 40, is_closed=False)
        db.record_bulk_closures([closed, opened], T0)
        db.record_bulk_closures([closed, opened], T0 + datetime.timedelta(hours=1))
        observed = db.get_permission_observations()
        assert len(observed) == 1
        assert observed.iloc[0].permission_observed_at == T0
        assert observed.iloc[0].is_closed
        assert pd.isna(observed.iloc[0].allow_deposits)
        append_permission_observation(db.con, PermissionObservation("later-unknown", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=2), provenance="observed_unknown"))
        db.record_bulk_closures([closed], T0 + datetime.timedelta(hours=3))
        state = select_permission_state(db.get_permission_observations(), pd.DataFrame({"vault_address": [ADDRESS], "timestamp": [T0 + datetime.timedelta(hours=4)]}))
        assert len(db.get_permission_observations()) == 3
        assert state.iloc[0].is_closed
        # A complete later vaultDetails closure also suppresses redundant partial
        # bulk denials, retaining its coherent flags and capacity inputs.
        append_permission_observation(db.con, PermissionObservation("closed-details", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=4), is_closed=True, allow_deposits=False, leader_fraction=0.15, capacity_observed_at=T0 + datetime.timedelta(hours=4), provenance="observed"))
        db.record_bulk_closures([closed], T0 + datetime.timedelta(hours=5))
        assert len(db.get_permission_observations()) == 4
        assert db.get_latest_leader_fractions() == {ADDRESS: 0.15}
    finally:
        db.close()


def test_duplicate_batch_preserves_sparse_values_and_first_write(tmp_path: Path) -> None:
    """Deduplicating one ingestion batch preserves sequential sparse semantics.

    A later price-only duplicate updates economics while retaining earlier sparse
    values and the first permission/write clock. It never creates a second key.

    :param tmp_path: Isolated file-backed database directory.
    :return: ``None`` after checking both incoming and already-stored duplicates.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "duplicates.duckdb")
    try:
        first = HyperliquidHighFreqPriceRow(ADDRESS, T0, 1, 100, 0, cumulative_volume=500, follower_count=7, apr=0.1, is_closed=False, allow_deposits=True, written_at=T0)
        last = HyperliquidHighFreqPriceRow(ADDRESS, T0, 2, 200, 1, written_at=T0 + datetime.timedelta(hours=1))
        db.upsert_high_freq_prices([first, last])
        row = db.get_all_high_freq_prices().iloc[0]
        assert row.share_price == 2
        assert row.cumulative_volume == 500
        assert row.follower_count == 7
        assert row.apr == pytest.approx(0.1)
        assert row.allow_deposits
        assert row.written_at == T0
        db.upsert_high_freq_prices([last, first, last])
        assert len(db.get_all_high_freq_prices()) == 1
        assert db.get_all_high_freq_prices().iloc[0].cumulative_volume == 500
    finally:
        db.close()


def test_failed_batch_rolls_back_and_accepts_next_batch(tmp_path: Path) -> None:
    """A mid-batch conversion failure leaves neither prices nor staging writes.

    Run real DuckDB inserts with a valid first row followed by invalid economics;
    then verify that rollback also allows the next successful ingestion.

    :param tmp_path: Isolated file-backed database directory.
    :return: ``None`` after checking rollback and successful retry.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "rollback.duckdb")
    try:
        row = HyperliquidHighFreqPriceRow(ADDRESS, T0, 1, 100, 0)
        invalid = list(row.as_db_tuple())
        invalid[2] = "not-a-number"
        columns = list(HyperliquidHighFreqPriceRow.__dataclass_fields__)
        with pytest.raises(duckdb.ConversionException):
            db.upsert_price_batch([row.as_db_tuple(), tuple(invalid)], columns)
        assert db.get_all_high_freq_prices().empty
        db.upsert_high_freq_prices([row])
        assert len(db.get_all_high_freq_prices()) == 1
    finally:
        db.close()


def test_capacity_lookup_excludes_inferred_and_superseded_inputs(tmp_path: Path) -> None:
    """Only the latest genuine coherent response can supply leader capital.

    New inferred annotations do not refresh capacity. A later genuine response
    omitting capacity clears the catalogue value rather than carrying it forwards.

    :param tmp_path: Isolated database directory.
    :return: ``None`` after checking provenance and successful Unknown precedence.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "capacity.duckdb")
    try:
        append_permission_observation(db.con, PermissionObservation("genuine", ADDRESS, permission_observed_at=T0, leader_fraction=0.15, capacity_observed_at=T0, provenance="observed"))
        append_permission_observation(db.con, PermissionObservation("inferred", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=1), leader_fraction=0.99, provenance="legacy_price_timestamp"))
        assert db.get_latest_leader_fractions() == {ADDRESS: 0.15}
        append_permission_observation(db.con, PermissionObservation("unknown", ADDRESS, permission_observed_at=T0 + datetime.timedelta(hours=2), provenance="observed_unknown"))
        assert db.get_latest_leader_fractions() == {}
    finally:
        db.close()


@pytest.mark.parametrize("pipeline", [daily_metrics, high_freq_metrics])
def test_scan_records_bulk_denial_before_address_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipeline: ModuleType) -> None:
    """Both scanners retain a drained vault's closure before price filtering.

    Select an unrelated address so no detail fetch can supply the denial;
    only the independently recorded bulk response can establish closure.

    :param tmp_path: Isolated scanner database directory.
    :param monkeypatch: Replace only the bulk provider response.
    :param pipeline: Daily or high-frequency collector module.
    :return: ``None`` after checking the filtered vault's retained denial.
    """
    summary = SimpleNamespace(vault_address=ADDRESS, is_closed=True, tvl=0, name="drained")
    monkeypatch.setattr(pipeline, "fetch_all_vaults", lambda *args, **kwargs: iter([summary]))
    scan = pipeline.run_daily_scan if pipeline is daily_metrics else pipeline.run_high_freq_scan
    db = scan(SimpleNamespace(), db_path=tmp_path / "filtered.duckdb", vault_addresses=["0x" + "b" * 40], flow_backfill_days=0, max_workers=1)
    try:
        observed = db.get_permission_observations()
        assert len(observed) == 1
        assert observed.iloc[0].vault_address == ADDRESS
        assert observed.iloc[0].is_closed
        assert pd.isna(observed.iloc[0].allow_deposits)
    finally:
        db.close()
