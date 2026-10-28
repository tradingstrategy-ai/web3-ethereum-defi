"""Exercise the single-command migration against real file-backed databases."""

# ruff: noqa: S608, PLR2004 - SQL identifiers and expected values are fixed fixture constants.

import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import duckdb
import pandas as pd
import pytest
from filelock import FileLock, Timeout

import eth_defi.hyperliquid.permission_recovery as recovery
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID

ADDRESS = "0x" + "a" * 40
PRICE_TIME = datetime.datetime(2026, 9, 21, 10)  # noqa: DTZ001 - Naive UTC source clock.
WRITE_TIME = datetime.datetime(2026, 10, 6)  # noqa: DTZ001 - Naive UTC write clock.


@pytest.fixture
def migration() -> ModuleType:
    """Load the actual operator entry point without executing its main function.

    Keep tests on the shipped script rather than maintaining a second wrapper.

    :return: Loaded Hyperliquid recovery script.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "hyperliquid" / "recover-permissions.py"
    spec = importlib.util.spec_from_file_location("recover_hyperliquid_permissions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_legacy_database(path: Path, name: str, rows: list[tuple]) -> None:
    """Create a checkpointed legacy database with the relevant constrained table.

    These tiny file-backed fixtures exercise the same schema migration and
    transaction path as retained scanner archives.

    :param path: New database filename.
    :param name: Daily or HF database identity.
    :param rows: Address, price timestamp, price, TVL, flags, leader fraction and write clock.
    :return: ``None`` after closing the database.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    table, clock, clock_type = ("vault_daily_prices", "date", "DATE") if name == "hyperliquid-vaults" else ("vault_high_freq_prices", "timestamp", "TIMESTAMP")
    connection = duckdb.connect(str(path))
    try:
        connection.execute(f"CREATE TABLE {table} (vault_address VARCHAR, {clock} {clock_type}, share_price DOUBLE, tvl DOUBLE, is_closed BOOLEAN, allow_deposits BOOLEAN, leader_fraction DOUBLE, written_at TIMESTAMP, PRIMARY KEY(vault_address,{clock}))")
        connection.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", rows)
    finally:
        connection.close()


@pytest.fixture
def pipeline(tmp_path: Path, migration: ModuleType) -> Path:
    """Create both targets, conflicting scanner archives and a raw-only HF point.

    Include an out-of-scope newer archive and unrelated pipeline state to
    detect accidental source widening or writes beyond the two databases.

    :param tmp_path: Temporary fixture root.
    :param migration: Actual script defining the fixed database identities.
    :return: Populated pipeline directory.
    """
    base = tmp_path / "vaults"
    for name in migration.DATABASE_NAMES:
        step = datetime.timedelta(days=1) if name == "hyperliquid-vaults" else datetime.timedelta(hours=1)
        create_legacy_database(base / f"{name}.duckdb", name, [(ADDRESS, PRICE_TIME, 2, 10, False, True, 0.1, WRITE_TIME)])
        create_legacy_database(base / "backups" / "2026-10-05" / f"{name}.duckdb", name, [(ADDRESS, PRICE_TIME, 1, 10, False, False, 0.1, PRICE_TIME), (ADDRESS, PRICE_TIME + step, 3, 10, False, False, 0.1, PRICE_TIME)])
        create_legacy_database(base / "backups" / "2026-09-29" / f"{name}.duckdb", name, [(ADDRESS, PRICE_TIME + step, 4, 10, False, True, 0.1, PRICE_TIME), (ADDRESS, PRICE_TIME + step * 2, 5, 10, None, None, None, PRICE_TIME)])
        create_legacy_database(base / "backups" / "2026-10-06" / f"{name}.duckdb", name, [(ADDRESS, PRICE_TIME + step * 10, 99, 10, False, True, 0.1, WRITE_TIME)])
    raw = pd.DataFrame({"chain": [HYPERCORE_CHAIN_ID], "address": [ADDRESS], "timestamp": [PRICE_TIME + datetime.timedelta(hours=3)], "share_price": [6.0], "total_assets": [10.0], "hypercore_source": ["hf"], "deposits_open": [True], "written_at": [PRICE_TIME]})
    raw.to_parquet(base / "backups" / "2026-10-05" / "vault-prices-1h.parquet", index=False)
    for name in ("vault-metadata-db.pickle", "vault-reader-state-1h.pickle", "vault-prices-1h.parquet"):
        (base / name).write_bytes(b"Unrelated pipeline state must remain unchanged")
    return base


def file_fingerprints(base: Path) -> dict[str, str]:
    """Fingerprint every persistent fixture file, including archives and state.

    Comparing the complete tree catches dry-run reports and lock files as well
    as database mutations.

    :param base: Fixture pipeline root.
    :return: Relative filename to SHA-256 mapping.
    """
    return {str(path.relative_to(base)): hashlib.sha256(path.read_bytes()).hexdigest() for path in base.rglob("*") if path.is_file()}


@pytest.mark.parametrize("dry_value", [None, "true"])
def test_script_dry_run_has_no_persistent_writes(pipeline: Path, migration: ModuleType, monkeypatch: pytest.MonkeyPatch, dry_value: str | None) -> None:
    """The default and explicit dry runs read both targets without any writes.

    No old migration variables or credentials are needed. Fingerprint all
    target, archive and unrelated files before invoking the real main function.

    :param pipeline: Populated fixture pipeline.
    :param migration: Loaded script entry point.
    :param monkeypatch: Isolated process environment configuration.
    :param dry_value: Explicit dry mode or absence of the only migration input.
    :return: ``None`` after checking the complete tree is unchanged.
    """
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(pipeline))
    monkeypatch.delenv("DRY_RUN", raising=False)
    if dry_value is not None:
        monkeypatch.setenv("DRY_RUN", dry_value)
    monkeypatch.setenv("TARGET_DATABASE", "/unused/old-target.duckdb")
    monkeypatch.setenv("BACKUP_SOURCES", "invalid old configuration")
    monkeypatch.setenv("PRE_MIGRATION_R2_KEY", "unused old configuration")
    before = file_fingerprints(pipeline)
    migration.main()
    assert file_fingerprints(pipeline) == before


def test_script_apply_backs_up_both_and_is_idempotent(pipeline: Path, migration: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """One apply restores both databases, keeps original economics and backs up.

    Newer selected archives beat older conflicts, raw-only HF keys are restored,
    and later unreviewed archives are excluded. A rerun inserts no extra keys.

    :param pipeline: Populated fixture pipeline.
    :param migration: Loaded script entry point.
    :param monkeypatch: Isolated process environment configuration.
    :return: ``None`` after verifying backups, transactions and rerun counts.
    """
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(pipeline))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("PRE_MIGRATION_R2_KEY", raising=False)
    monkeypatch.delenv("REQUIRE_OFFHOST_BACKUP", raising=False)
    before = file_fingerprints(pipeline)
    migration.main()
    report_path = pipeline / "migration-backups" / "hypercore-permissions-1628" / "recovery-report.json"
    reports = json.loads(report_path.read_text())
    assert set(reports) == set(migration.DATABASE_NAMES)
    for name, report in reports.items():
        backup = Path(report["backup"]["backup"])
        assert backup.is_file()
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == before[f"{name}.duckdb"]
        assert backup.with_suffix(".json").is_file()
        assert all("2026-10-06" not in source["path"] for source in report["sources"])
        table = "vault_daily_prices" if name == "hyperliquid-vaults" else "vault_high_freq_prices"
        clock = "date" if name == "hyperliquid-vaults" else "timestamp"
        expected_prices = [2, 3, 5] if name == "hyperliquid-vaults" else [2, 3, 5, 6]
        connection = duckdb.connect(str(pipeline / f"{name}.duckdb"), read_only=True)
        try:
            assert [row[0] for row in connection.execute(f"SELECT share_price FROM {table} ORDER BY {clock}").fetchall()] == expected_prices
            assert connection.execute(f"SELECT written_at FROM {table} ORDER BY {clock} LIMIT 1").fetchone()[0] == WRITE_TIME
            assert connection.execute(f"SELECT count(*) FROM {table} WHERE is_closed IS NOT NULL OR allow_deposits IS NOT NULL").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM duckdb_constraints() WHERE constraint_type IN ('PRIMARY KEY','UNIQUE')").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM vault_permission_observations WHERE provenance='legacy_price_timestamp' AND allow_deposits=false").fetchone()[0] == 2
            assert report["tables"][table]["rows_after"] == len(expected_prices)
        finally:
            connection.close()
    after = file_fingerprints(pipeline)
    assert all(after[path] == digest for path, digest in before.items() if path.startswith("backups/") or not path.endswith(".duckdb"))
    migration.main()
    repeated = json.loads(report_path.read_text())
    assert all(next(iter(report["tables"].values()))["restore_missing"] == 0 for report in repeated.values())
    assert {name: report["permission_records"] for name, report in repeated.items()} == {name: report["permission_records"] for name, report in reports.items()}


def test_script_apply_refuses_active_pipeline_writer(pipeline: Path, migration: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A held pipeline lock prevents either database from being changed.

    Use the real FileLock mechanism with a bounded immediate test timeout.

    :param pipeline: Populated fixture pipeline.
    :param migration: Loaded script entry point.
    :param monkeypatch: Replace only the lock timeout for a fast contention check.
    :return: ``None`` after confirming no persistent changes under contention.
    """
    original = migration.wait_other_writers
    monkeypatch.setattr(migration, "wait_other_writers", lambda path, **_kwargs: original(path, timeout=0))
    with FileLock(pipeline / "scan-pipeline.lock"):
        before = file_fingerprints(pipeline)
        with pytest.raises(Timeout):
            migration.migrate_databases(pipeline, dry_run=False)
        assert file_fingerprints(pipeline) == before


def test_script_missing_target_aborts_before_writes(pipeline: Path, migration: ModuleType) -> None:
    """An absent second target cannot leave the first database half-migrated.

    Both target filenames are checked before acquiring the writer lock or
    beginning the first database repair.

    :param pipeline: Populated fixture pipeline.
    :param migration: Loaded script entry point.
    :return: ``None`` after confirming the remaining tree is unchanged.
    """
    (pipeline / "hyperliquid-vaults-hf.duckdb").unlink()
    before = file_fingerprints(pipeline)
    with pytest.raises(FileNotFoundError, match="hyperliquid-vaults-hf"):
        migration.migrate_databases(pipeline, dry_run=False)
    assert file_fingerprints(pipeline) == before


def test_script_backup_failure_preserves_database(pipeline: Path, migration: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A local backup failure aborts before any repair transaction starts.

    Simulate an actual filesystem error in the shared backup primitive and
    verify both targets and all archives keep their original fingerprints.

    :param pipeline: Populated fixture pipeline.
    :param migration: Loaded script entry point.
    :param monkeypatch: Inject an unavailable backup destination.
    :return: ``None`` after confirming the failed run leaves database contents intact.
    """

    def fail_backup(_connection: duckdb.DuckDBPyConnection, _path: Path, _backup_dir: Path) -> dict:
        """Simulate a filesystem failure before mutation.

        :param _connection: Exclusive target connection.
        :param _path: Target database filename.
        :param _backup_dir: Intended automatic backup directory.
        :return: Never returns; raises the simulated I/O failure.
        """
        msg = "Backup destination unavailable"
        raise OSError(msg)

    monkeypatch.setattr(recovery, "backup_database", fail_backup)
    before = file_fingerprints(pipeline)
    with pytest.raises(OSError, match="Backup destination unavailable"):
        migration.migrate_databases(pipeline, dry_run=False)
    after = file_fingerprints(pipeline)
    assert all(after[path] == digest for path, digest in before.items())
    assert not (pipeline / "migration-backups").exists()
