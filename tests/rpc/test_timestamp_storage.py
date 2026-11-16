"""Exercise timestamp layout, exclusive replacement and interrupted publication."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.event_reader.timestamp_lock import timestamp_access_lock
from eth_defi.event_reader.timestamp_storage import build_timestamp_candidate, file_digest, validate_timestamp_files
from eth_defi.vault_protocols import migration as migration_runner
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import timestamp_storage as repair


def test_manual_chain_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope manual apply and its retry journal without touching another chain."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source, other = root / "43114-timestamps.duckdb", root / "1-timestamps.duckdb"
    create_cache(source)
    create_cache(other)
    before = file_digest(source), file_digest(other)
    script = Path(__file__).parents[2] / "migration/scripts/065-compact-block-timestamp-caches.py"
    entry = runpy.run_path(str(script))
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    monkeypatch.setenv("TIMESTAMP_CHAIN_IDS", "43114")
    monkeypatch.setenv("DRY_RUN", "true")
    entry["main"]()
    assert (file_digest(source), file_digest(other)) == before
    assert not (root / ".timestamp-maintenance/43114/migration.json").exists()
    monkeypatch.setenv("DRY_RUN", "false")
    entry["main"]()
    after = file_digest(source)
    assert after != before[0]
    assert file_digest(other) == before[1]
    journal = root / ".timestamp-maintenance/43114/migration.json"
    record = json.loads(journal.read_text())[script.name]
    assert record["success"]
    assert set(record["other_data"]["storage_recovery"]) == {str(source)}
    recovery = record["other_data"]["storage_recovery"][str(source)]
    assert recovery["phase"] == "published"
    assert file_digest(Path(recovery["backup"])) == before[0]
    entry["main"]()
    assert file_digest(source) == after
    with pytest.raises(ValueError, match="manual entry point"):
        entry["migrate"](MigrationContext(tmp_path, False))


def create_cache(path: Path, rows: int = 100_000) -> None:
    """Create an unordered file with compactable integer sequences.

    Multiplication by 7919, coprime to the row counts used here, permutes the
    range without losing rows.

    :param path: New test database.
    :param rows: Number of unique blocks.
    :return: ``None`` after closing the database.
    """
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE block_timestamps(block_number UBIGINT,timestamp UINTEGER)")
        con.execute(f"INSERT INTO block_timestamps SELECT (i*7919)%{rows},1700000000+((i*7919)%{rows})//2 FROM range({rows}) r(i)")
    finally:
        con.close()


def test_sorted_candidate_preserves_every_row(tmp_path: Path) -> None:
    """Check exact equality, reopen and a real reduction in a file-backed cache."""
    source, target = tmp_path / "source.duckdb", tmp_path / "target.duckdb"
    create_cache(source)
    before = file_digest(source)
    result = build_timestamp_candidate(source, target, tmp_path)
    assert result["rows"] == 100_000
    assert result["bytes"] < source.stat().st_size
    assert file_digest(source) == before
    assert validate_timestamp_files(source, target, tmp_path) == 100_000


def test_legacy_primary_key_candidate_preserves_every_row(tmp_path: Path) -> None:
    """Accept the legacy constraint while preserving its complete file contents."""
    source, target = tmp_path / "source.duckdb", tmp_path / "target.duckdb"
    create_cache(source)
    con = duckdb.connect(str(source))
    try:
        con.execute("ALTER TABLE block_timestamps ADD PRIMARY KEY (block_number)")
    finally:
        con.close()
    before = file_digest(source)
    result = build_timestamp_candidate(source, target, tmp_path)
    assert result["rows"] == 100_000
    assert result["constraints"] == []
    assert file_digest(source) == before


@pytest.mark.parametrize("anomaly", ["duplicate", "null", "extra", "index", "view"])
def test_invalid_cache_is_rejected(tmp_path: Path, anomaly: str) -> None:
    """Reject ambiguous contents rather than discarding or deduplicating rows."""
    source = tmp_path / "source.duckdb"
    create_cache(source)
    con = duckdb.connect(str(source))
    try:
        con.execute(
            {
                "duplicate": "INSERT INTO block_timestamps VALUES (0,1700000001)",
                "null": "INSERT INTO block_timestamps VALUES (NULL,NULL)",
                "extra": "CREATE TABLE unrelated (value INT)",
                "index": "CREATE INDEX custom_timestamp_index ON block_timestamps(timestamp)",
                "view": "CREATE VIEW timestamp_view AS SELECT * FROM block_timestamps",
            }[anomaly]
        )
    finally:
        con.close()
    before = file_digest(source)
    with pytest.raises(ValueError):
        build_timestamp_candidate(source, tmp_path / "target.duckdb", tmp_path)
    assert before == file_digest(source)


def test_access_lock_excludes_open_cache(tmp_path: Path) -> None:
    """Keep the stable directory locked until the cache connection is closed."""
    database = BlockTimestampDatabase(1, tmp_path / "1-timestamps.duckdb")
    try:
        with pytest.raises(BlockingIOError):
            with timestamp_access_lock(tmp_path, exclusive=True):
                pytest.fail("Exclusive lock acquired while cache is open")
    finally:
        database.close()
    with timestamp_access_lock(tmp_path, exclusive=True):
        with pytest.raises(BlockingIOError):
            BlockTimestampDatabase(1, tmp_path / "1-timestamps.duckdb")


def test_unsigned_ranges_and_ordered_updates(tmp_path: Path) -> None:
    """Preserve inclusive ranges, corrections and values above signed int64."""
    database = BlockTimestampDatabase(1, tmp_path / "1-timestamps.duckdb")
    try:
        maximum = 2**64 - 1
        database.import_chain_data(1, pd.Series([1700000001, 1700000000, 1700000002], index=pd.Index([maximum, 0, maximum - 1], dtype="uint64")))
        database.import_chain_data(1, pd.Series([1800000000], index=pd.Index([maximum], dtype="uint64")))
        assert database.query(-10, 0).index.tolist() == [0]
        assert database.query(-10, -1).empty
        assert database.query(maximum + 1, maximum + 2).empty
        assert database.query(maximum - 1, maximum + 1).index.tolist() == [maximum - 1, maximum]
        assert database.query(maximum, maximum).iloc[0] == pd.Timestamp(1800000000, unit="s")
        assert database.get_missing_block_numbers([maximum, maximum - 2]) == [maximum - 2]
    finally:
        database.close()


@pytest.mark.parametrize("dry_run", [True, False])
def test_compaction_preview_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool) -> None:
    """Keep preview bytes unchanged and preserve independent backups on apply."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    before = file_digest(source)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    context = MigrationContext(tmp_path, dry_run, script_name="065-compact-block-timestamp-caches.py")
    repair.compact_timestamp_caches(context)
    if dry_run:
        assert before == file_digest(source)
        assert not (root / ".timestamp-recovery").exists()
    else:
        entry = context.other_data["storage_recovery"][str(source)]
        assert entry["phase"] == "published"
        backup = Path(entry["backup"])
        assert file_digest(backup) == before
        assert backup.stat().st_ino != source.stat().st_ino
        repair.compact_timestamp_caches(context)
        assert validate_timestamp_files(backup, source, tmp_path) == 100_000


def test_retry_after_rename_before_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconcile a published inode from the durable pre-rename intent."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    context = MigrationContext(tmp_path, False, script_name="065-compact-block-timestamp-caches.py")
    persisted = []
    context._persist = lambda: persisted.append(json.loads(json.dumps(context.other_data)))
    original = os.replace

    def interrupted_replace(src: Path, dst: Path) -> None:
        """Simulate a native interruption immediately after canonical rename."""
        original(src, dst)
        if dst == source:
            raise RuntimeError("Interrupted after publication")

    with monkeypatch.context() as patch:
        patch.setattr(repair.os, "replace", interrupted_replace)
        with pytest.raises(RuntimeError, match="Interrupted"):
            repair.compact_timestamp_caches(context)
    retried = MigrationContext(tmp_path, False, script_name=context.script_name, other_data=persisted[-1])
    repair.compact_timestamp_caches(retried)
    assert retried.other_data["storage_recovery"][str(source)]["phase"] == "published"


def test_large_timestamp_ingestion(tmp_path: Path) -> None:
    """Opt into production-scale file-backed ingestion without live credentials."""
    if not os.environ.get("RUN_LARGE_TIMESTAMP_TEST"):
        pytest.skip("Set RUN_LARGE_TIMESTAMP_TEST=1 for the manual 90-million-row check")
    source = tmp_path / "source.duckdb"
    create_cache(source, 90_000_000)
    target = tmp_path / "target.duckdb"
    build_timestamp_candidate(source, target, tmp_path)
    database = BlockTimestampDatabase(1, target)
    try:
        incoming = pd.Series([1800000000, 1800000001, 1800000002], index=[0, 89_999_999, 90_000_000])
        database.import_chain_data(1, incoming)
        database.import_chain_data(1, incoming)
        assert database.get_count() == 90_000_001
        assert database.query(90_000_000, 90_000_000).iloc[0] == pd.Timestamp(1800000002, unit="s")
    finally:
        database.close()


def test_preflight_all_chains_before_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid later cache must leave every earlier canonical file unchanged."""
    root = tmp_path / "timestamps"
    root.mkdir()
    first, second = root / "1-timestamps.duckdb", root / "2-timestamps.duckdb"
    create_cache(first)
    create_cache(second)
    con = duckdb.connect(str(second))
    try:
        con.execute("INSERT INTO block_timestamps VALUES (NULL,NULL)")
    finally:
        con.close()
    original = file_digest(first)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    with pytest.raises(ValueError):
        repair.compact_timestamp_caches(MigrationContext(tmp_path, False, script_name="065-test.py"))
    assert file_digest(first) == original
    assert not (root / ".timestamp-recovery").exists()


def test_snapshot_context_requires_timestamp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated runner must never fall back to the operator's real caches."""
    monkeypatch.delenv("TIMESTAMP_CACHE", raising=False)
    with pytest.raises(ValueError, match="explicit TIMESTAMP_CACHE"):
        repair.compact_timestamp_caches(MigrationContext(tmp_path, False))


def append_uncheckpointed_timestamp(source: Path) -> None:
    """Leave a real WAL containing one committed timestamp on the test cache.

    Exit the subprocess without closing its connection so reopening must
    recover the paired database and WAL.

    :param source: Closed 100,000-row test cache.
    :return: ``None`` after the subprocess exits.
    """
    program = "import duckdb,os,sys; c=duckdb.connect(sys.argv[1]); c.execute('INSERT INTO block_timestamps VALUES (100000,1700050000)'); os._exit(0)"
    subprocess.run([sys.executable, "-c", program, str(source)], check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("damage", ["corrupted_copy", "missing_source"])
def test_backup_rejects_changed_wal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str) -> None:
    """Refuse incomplete recovery evidence before checkpointing or publication."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    append_uncheckpointed_timestamp(source)
    wal = Path(str(source) + ".wal")
    before = file_digest(source), file_digest(wal)
    original_copy = repair.durable_copy

    def damaged_copy(src: Path, dst: Path) -> None:
        """Simulate damaged backup bytes or a writer ignoring maintenance locks."""
        original_copy(src, dst)
        if damage == "corrupted_copy" and src == wal:
            with dst.open("ab") as stream:
                stream.write(b"corrupted")
        elif damage == "missing_source" and src == source:
            wal.unlink()

    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    monkeypatch.setattr(repair, "durable_copy", damaged_copy)
    context = MigrationContext(tmp_path, False, script_name="065-test.py")
    with pytest.raises(ValueError, match="Backup WAL does not match frozen source"):
        repair.compact_timestamp_caches(context)
    assert file_digest(source) == before[0]
    assert "backup" not in context.other_data["storage_recovery"][str(source)]
    if damage == "corrupted_copy":
        assert file_digest(wal) == before[1]
    else:
        assert not wal.exists()


@pytest.mark.parametrize("interrupt_checkpoint", [False, True])
def test_wal_preview_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt_checkpoint: bool) -> None:
    """Preserve source WAL bytes in preview and retain paired backups on apply."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    append_uncheckpointed_timestamp(source)
    wal = Path(str(source) + ".wal")
    assert wal.exists()
    before = file_digest(source), file_digest(wal)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    repair.compact_timestamp_caches(MigrationContext(tmp_path, True, script_name="065-test.py"))
    assert (file_digest(source), file_digest(wal)) == before
    context = MigrationContext(tmp_path, False, script_name="065-test.py")
    if interrupt_checkpoint:
        persisted = []

        def persist() -> None:
            """Interrupt after native checkpoint but before the next journal write."""
            entry = context.other_data.get("storage_recovery", {}).get(str(source), {})
            if entry.get("phase") == "backed_up" and not wal.exists():
                raise RuntimeError("Interrupted checkpoint")
            persisted.append(json.loads(json.dumps(context.other_data)))

        context._persist = persist
        with pytest.raises(RuntimeError, match="Interrupted checkpoint"):
            repair.compact_timestamp_caches(context)
        context = MigrationContext(tmp_path, False, script_name="065-test.py", other_data=persisted[-1])
    repair.compact_timestamp_caches(context)
    entry = context.other_data["storage_recovery"][str(source)]
    backup = Path(entry["backup"])
    assert (file_digest(backup), file_digest(Path(str(backup) + ".wal"))) == before
    assert not wal.exists()
    assert entry["rows"] == 100001


def test_runner_resumes_partial_multi_chain_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Carry durable recovery records through a failed runner and a new attempt."""
    root = tmp_path / "timestamps"
    root.mkdir()
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    name = "065-compact-block-timestamp-caches.py"
    shutil.copy2(Path(__file__).parents[2] / "migration/scripts" / name, scripts / name)
    first, second = root / "1-timestamps.duckdb", root / "2-timestamps.duckdb"
    create_cache(first)
    create_cache(second)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    original = os.replace

    def interrupted_replace(src: Path, dst: Path) -> None:
        """Interrupt the batch immediately after publishing its first chain."""
        original(src, dst)
        if dst == first:
            raise RuntimeError("Interrupted batch")

    version = {"git_commit_hash": "a" * 40, "in_docker": False}
    with monkeypatch.context() as patch:
        patch.setattr(repair.os, "replace", interrupted_replace)
        with pytest.raises(RuntimeError, match="Interrupted batch"):
            migration_runner.run_pending_migrations(data_dir=pipeline, scripts_dir=scripts, dry_run=False, provenance=version)
    before = first.stat().st_ino
    assert migration_runner.run_pending_migrations(data_dir=pipeline, scripts_dir=scripts, dry_run=False, provenance=version) == [name]
    assert first.stat().st_ino == before
    journal = json.loads((pipeline / "migration.json").read_text())[name]
    assert journal["success"]
    assert all(entry["phase"] == "published" for entry in journal["other_data"]["storage_recovery"].values())


def test_empty_cache_and_negative_ingestion(tmp_path: Path) -> None:
    """Keep empty caches readable and reject negative blocks before conversion."""
    source = tmp_path / "source.duckdb"
    database = BlockTimestampDatabase(1, source)
    try:
        assert database.query(0, 1).empty
        with pytest.raises(ValueError, match="uint64"):
            database.import_chain_data(1, pd.Series([1700000000], index=[-1]))
    finally:
        database.close()
    assert build_timestamp_candidate(source, tmp_path / "target.duckdb", tmp_path)["rows"] == 0


@pytest.mark.parametrize("changed", ["backup", "backup_wal", "unexpected_backup_wal", "published", "published_wal", "inventory"])
def test_recovery_refuses_unexplained_divergence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    """Never accept damaged evidence or concurrent writes on an interrupted run."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    if changed == "backup_wal":
        append_uncheckpointed_timestamp(source)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    context = MigrationContext(tmp_path, False, script_name="065-test.py")
    repair.compact_timestamp_caches(context)
    entry = context.other_data["storage_recovery"][str(source)]
    if changed == "inventory":
        create_cache(root / "2-timestamps.duckdb")
    elif changed == "published_wal":
        append_uncheckpointed_timestamp(source)
    elif changed == "published":
        con = duckdb.connect(str(source))
        try:
            con.execute("INSERT INTO block_timestamps VALUES (100000,1700000000)")
        finally:
            con.close()
    else:
        damaged = Path(entry["backup"] + (".wal" if changed in {"backup_wal", "unexpected_backup_wal"} else ""))
        if changed == "backup_wal":
            assert entry["backup_wal_hash"] is not None
        with damaged.open("ab") as stream:
            stream.write(b"corrupted")
    before = file_digest(source)
    with pytest.raises(ValueError):
        repair.compact_timestamp_caches(context)
    assert file_digest(source) == before


def test_migration_refuses_open_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give an actionable error while an earlier batch accessor remains open."""
    root = tmp_path / "timestamps"
    root.mkdir()
    source = root / "1-timestamps.duckdb"
    create_cache(source)
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    database = BlockTimestampDatabase(1, source)
    try:
        with pytest.raises(BlockingIOError, match="Timestamp cache in use"):
            repair.compact_timestamp_caches(MigrationContext(tmp_path, False))
    finally:
        database.close()


def test_unchanged_file_does_not_create_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty file that cannot shrink is a reported no-op."""
    root = tmp_path / "timestamps"
    root.mkdir()
    database = BlockTimestampDatabase(1, root / "1-timestamps.duckdb")
    database.close()
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    repair.compact_timestamp_caches(MigrationContext(tmp_path, False))
    assert not (root / ".timestamp-recovery").exists()
    assert not list(root.glob(".timestamp-compact-*"))
