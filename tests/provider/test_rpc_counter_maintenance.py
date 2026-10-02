"""File-backed recovery and preservation tests for counter maintenance."""

import datetime
import json
import runpy
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from eth_defi.provider import rpc_counter_maintenance as maintenance
from eth_defi.provider.rpc_counter_comparison import compare_rpc_counter_windows, fetch_rpc_counter_window
from eth_defi.provider.rpcdb import RPCRequestStats, RPCUsageDatabase
from eth_defi.version_info import VersionInfo


@pytest.fixture()
def counter_path(tmp_path: Path) -> Path:
    """Seed both counter tables and operation detail with a high cycle."""
    path = tmp_path / "rpc-tracking.duckdb"
    with RPCUsageDatabase(path) as database:
        stats = RPCRequestStats(operation="historical_multicall")
        stats.record_call("rpc.example", "eth_call", 123)
        stats.record_error("rpc.example", "http_429", "private provider message")
        database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 776, stats, 3)
    return path


def test_reset_verified_backup_and_monotonic_rollback(counter_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reset preserves original evidence and old allocator compatibility."""
    unrelated = tmp_path / "reader-state.pickle"
    unrelated.write_bytes(b"critical historical state")
    version = VersionInfo(commit_hash="test-image-commit")
    monkeypatch.setattr(maintenance.VersionInfo, "read_docker_version", lambda: version)
    now = datetime.datetime(2026, 9, 30, 15, 0)
    result = maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "rollout-2026-09-30", now, protected_paths=(unrelated,))
    assert result["maintenance_version"] == version.as_dict()
    backup = Path(result["backup_path"])
    assert backup.name == "rpc-tracking-before-rpc-reduction-2026-09-30T150000Z.duckdb"
    assert backup.stat().st_mode & 0o777 == 0o600
    with duckdb.connect(str(backup), read_only=True) as connection:
        assert maintenance.fetch_counter_inventory(connection) == result["tables"]
    with RPCUsageDatabase(counter_path) as database:
        assert database.allocate_cycle() == 777
        connection = database._require_connection()
        assert connection.execute("SELECT phase, call_count, items_scanned FROM vault_rpc_api_calls").fetchall() == [("counter_reset", 0, 0)]
        assert connection.execute("SELECT count(*) FROM vault_rpc_api_errors").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM vault_rpc_operation_calls").fetchone()[0] == 0
        # The exact legacy allocator still works without reading the epoch table.
        assert connection.execute("SELECT max(cycle_number)+1 FROM (SELECT cycle_number FROM vault_rpc_api_calls UNION ALL SELECT cycle_number FROM vault_rpc_api_errors)").fetchone()[0] == 777
        stats = RPCRequestStats()
        stats.record_call("rpc.example", "eth_call", 9)
        database.record_scan(1, "price_scan", now.date(), 777, stats, 1)
    recovered = maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "rollout-2026-09-30")
    assert recovered["backup_path"] == str(backup)
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 9
    assert result["protected_state_sha256"][str(unrelated.resolve())] == maintenance.fetch_protected_state_digests((unrelated,))[str(unrelated.resolve())]
    assert unrelated.read_bytes() == b"critical historical state"


def test_snapshot_and_collision_do_not_reset(counter_path: Path, tmp_path: Path) -> None:
    """Snapshot-only operation and timestamp collisions preserve counters."""
    now = datetime.datetime(2026, 9, 30)
    result = maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", now=now)
    assert not result["reset_committed"]
    with pytest.raises(FileExistsError):
        maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", now=now)
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 123


def test_append_after_verification_aborts_reset(counter_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A writer bypassing the lock cannot have unbacked rows deleted."""
    original = maintenance._write_private_json

    def append_after_manifest(path: Path, data: dict) -> None:
        original(path, data)
        with RPCUsageDatabase(counter_path) as database:
            stats = RPCRequestStats()
            stats.record_call("rpc.example", "eth_call", 7)
            database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 777, stats, 1)

    monkeypatch.setattr(maintenance, "_write_private_json", append_after_manifest)
    with pytest.raises(RuntimeError, match="changed after backup"):
        maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "concurrent-append")
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 130
        assert connection.execute("SELECT sum(error_count) FROM vault_rpc_api_errors").fetchone()[0] == 1


@pytest.mark.parametrize("boundary", ["copy", "verification", "commit"])
def test_process_death_recovery(counter_path: Path, tmp_path: Path, boundary: str) -> None:
    """Real process death around backup and commit never loses evidence."""
    code = """
import os, sys, datetime
from pathlib import Path
from eth_defi.provider import rpc_counter_maintenance as m
boundary = sys.argv[3]
if boundary == "copy":
    def die_copy(source, target, length):
        target.write(source.read(64))
        target.flush()
        os._exit(91)
    m.shutil.copyfileobj = die_copy
else:
    original = m._write_private_json
    def die_receipt(path, data):
        if boundary == "commit" and str(path).endswith(".completed.json"):
            os._exit(91)
        original(path, data)
        if boundary == "verification" and not str(path).endswith(".completed.json"):
            os._exit(91)
    m._write_private_json = die_receipt
m.snapshot_and_reset_rpc_counters(Path(sys.argv[1]), Path(sys.argv[2]), "interrupted", datetime.datetime(2026, 9, 30))
"""
    result = subprocess.run([sys.executable, "-c", code, str(counter_path), str(tmp_path / "backups"), boundary], timeout=60, capture_output=True)
    assert result.returncode == 91, result.stderr.decode()
    receipt = maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "interrupted", datetime.datetime(2026, 9, 30, 0, 0, 1))
    assert receipt["reset_committed"]
    with duckdb.connect(str(receipt["backup_path"]), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 123
    with RPCUsageDatabase(counter_path) as database:
        assert database.allocate_cycle() == 777


def test_copy_failure_leaves_original_counters(counter_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Insufficient space or copy failure cannot reach the delete transaction."""

    def fail_copy(*args, **kwargs) -> None:
        raise OSError("no space left")

    monkeypatch.setattr(maintenance.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="no space"):
        maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "disk-full")
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 123


def test_counter_window_comparison_deduplicates_items(counter_path: Path, tmp_path: Path) -> None:
    """Explicit windows normalise rates without double-counting method items."""
    before = fetch_rpc_counter_window(counter_path, datetime.date(2026, 9, 29), datetime.date(2026, 10, 1))
    after = fetch_rpc_counter_window(counter_path, datetime.date(2026, 9, 30), datetime.date(2026, 10, 1))
    assert before["calls"] == 123
    assert before["calls_per_day"] == 61.5
    assert after["calls_per_day"] == 123
    rows = compare_rpc_counter_windows(before, after)
    assert rows[0]["reduction_percent"] == -100
    assert before["operations"]
    with pytest.raises(ValueError, match="end must be after"):
        fetch_rpc_counter_window(counter_path, datetime.date(2026, 10, 1), datetime.date(2026, 10, 1))


def test_protected_state_change_aborts_reset(counter_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A writer bypassing the pipeline lock cannot reset over changed history."""
    protected = tmp_path / "reader-state.pickle"
    protected.write_bytes(b"original reader progress")
    original = maintenance._write_private_json

    def write_then_change(path: Path, data: dict) -> None:
        original(path, data)
        protected.write_bytes(b"concurrent reader progress")

    monkeypatch.setattr(maintenance, "_write_private_json", write_then_change)
    with pytest.raises(RuntimeError, match="Protected pipeline state changed"):
        maintenance.snapshot_and_reset_rpc_counters(counter_path, tmp_path / "backups", "protected-conflict", protected_paths=(protected,))
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 123


def test_snapshot_script_protects_canonical_cache_with_custom_pipeline(counter_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A custom pipeline directory does not hide the canonical timestamp cache."""
    pipeline = tmp_path / "custom-pipeline"
    pipeline.mkdir()
    reader = pipeline / "readers.pickle"
    reader.write_bytes(b"critical reader progress")
    caches = (tmp_path / "canonical-cache", tmp_path / "block-timestamp")
    for folder in caches:
        folder.mkdir()
        (folder / "1-timestamps.duckdb").write_bytes(b"critical dense timestamps")
    backup_dir = tmp_path / "private-backups"
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(pipeline))
    monkeypatch.setenv("RPC_TRACKING_DATABASE_PATH", str(counter_path))
    monkeypatch.setenv("RPC_COUNTER_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("BACKUP_RPC_COUNTERS", "true")
    monkeypatch.setenv("RESET_RPC_COUNTERS", "false")
    script = Path(__file__).parents[2] / "scripts/erc-4626/reset-rpc-counters.py"
    main = runpy.run_path(str(script))["main"]
    monkeypatch.setitem(main.__globals__, "DEFAULT_TIMESTAMP_CACHE_FOLDER", caches[0])
    monkeypatch.setitem(main.__globals__, "setup_console_logging", lambda _level: None)
    main()
    (manifest_path,) = backup_dir.glob("*.json")
    manifest = json.loads(manifest_path.read_text())
    protected = manifest["protected_state_sha256"]
    assert str(reader.resolve()) in protected
    assert all(str((folder / "1-timestamps.duckdb").resolve()) in protected for folder in caches)
    assert not manifest["reset_committed"]
    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 123


def test_accounting_serialisation_failure_rolls_back(counter_path: Path) -> None:
    """Unexpected metric serialisation cannot leave the next phase in a transaction."""

    class BrokenMetric:
        def __str__(self) -> str:
            raise ValueError("cannot serialise metric")

    with RPCUsageDatabase(counter_path) as database:
        with pytest.raises(ValueError, match="cannot serialise"):
            database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 777, RPCRequestStats(), 1, metrics={"metric": BrokenMetric()})
        database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 777, RPCRequestStats(), 1)
        assert database._require_connection().execute("SELECT count(*) FROM vault_rpc_api_calls WHERE cycle_number=777").fetchone()[0] == 1
