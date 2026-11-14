# ruff: noqa: PLR2004 -- fixed synthetic cache row counts.
"""File-backed coverage for additive, resumable timestamp repairs."""

import datetime
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.vault_protocols.migration import MigrationContext, run_pending_migrations
from eth_defi.vault_protocols.repairs import dense_timestamps


@pytest.fixture
def repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MigrationContext, Path]:
    """Create a sparse cache and guarded migration context without live APIs.

    Only provider reads are replaced. Copies, recovery state, database writes,
    validation and atomic publication use the real implementation.

    :param tmp_path: Isolated database and migration paths.
    :param monkeypatch: Configure isolated state and a deterministic provider.
    :return: Apply context and cache directory.
    """
    root = tmp_path / "timestamps"
    database = BlockTimestampDatabase.create(42161, root)
    try:
        database.import_chain_data(42161, pd.Series({1000: 1700001000, 1010: 1700001010, 1030: 1700001030}))
    finally:
        database.close()
    monkeypatch.setenv("TIMESTAMP_CACHE", str(root))
    monkeypatch.setattr(dense_timestamps, "configure_hypersync_from_env", lambda _: SimpleNamespace(hypersync_client=object()))

    def fetch_timestamps(_client: object, chain_id: int, first: int, last: int, cache_path: Path, **_kwargs: object) -> object:
        """Supply deterministic missing headers through the existing writer."""
        database = BlockTimestampDatabase(chain_id, cache_path / f"{chain_id}-timestamps.duckdb")
        database.import_chain_data(chain_id, pd.Series({number: 1700000000 + number for number in range(first, last + 1)}))
        return database.get_slicer()

    monkeypatch.setattr(dense_timestamps, "fetch_block_timestamps_using_hypersync_cached", fetch_timestamps)
    context = MigrationContext(data_dir=tmp_path / "pipeline", dry_run=False, script_name="070-dense-block-timestamp-caches.py")
    return context, root


def test_dense_timestamp_preview_preserves_files(repair: tuple[MigrationContext, Path]) -> None:
    """Report missing blocks without creating backups or changing cache bytes.

    Preview is an inventory operation; it never starts the provider collector.

    :param repair: Isolated sparse cache and migration context.
    :return: None after checking the report and byte identity.
    """
    context, root = repair
    context.dry_run = True
    path = root / "42161-timestamps.duckdb"
    before = path.read_bytes()
    dense_timestamps.repair_dense_timestamp_caches(context)
    assert path.read_bytes() == before
    assert set(root.iterdir()) == {path}
    assert context.other_data["dense_42161"]["missing_rows"] == 28
    assert not context.modified_files


@pytest.mark.parametrize("dry_run", [True, False])
def test_dense_timestamp_space_preflight_covers_sparse_growth(repair: tuple[MigrationContext, Path], monkeypatch: pytest.MonkeyPatch, *, dry_run: bool) -> None:
    """Budget the whole dense inventory before changing the first chain.

    A tiny second file spans a million blocks. Disk space sufficient under the
    old file-size heuristic must be refused before even the small first repair
    writes a backup or candidate; preview still reports the shortage.

    :param repair: Isolated sparse Arbitrum cache and migration context.
    :param monkeypatch: Supply a filesystem with limited free space.
    :param dry_run: Whether to report the plan or refuse application.
    :return: None after checking both original files and the space report.
    """
    context, root = repair
    context.dry_run = dry_run
    database = BlockTimestampDatabase.create(8453, root)
    try:
        database.import_chain_data(8453, pd.Series({1: 1700000001, 1_000_000: 1701000000}))
    finally:
        database.close()
    original = {path: path.read_bytes() for path in root.glob("*-timestamps.duckdb")}
    monkeypatch.setattr(dense_timestamps.shutil, "disk_usage", lambda _path: SimpleNamespace(free=128 * 1024**2))
    if dry_run:
        dense_timestamps.repair_dense_timestamp_caches(context)
    else:
        with pytest.raises(OSError, match="Insufficient free space for dense timestamp repair"):
            dense_timestamps.repair_dense_timestamp_caches(context)
    space = context.other_data["dense_timestamp_space"]
    assert space["required_free_bytes"] > space["available_free_bytes"]
    assert set(space["per_chain_bytes"]) == {"42161", "8453"}
    assert {path: path.read_bytes() for path in root.glob("*-timestamps.duckdb")} == original
    assert not (root / ".timestamp-recovery").exists()
    assert not context.modified_files


def test_dense_timestamp_apply_and_repeat(repair: tuple[MigrationContext, Path]) -> None:
    """Publish complete coverage while retaining original rows and backup bytes.

    Rerunning the helper after publication must not create another backup.

    :param repair: Isolated sparse cache and migration context.
    :return: None after validating row accounting and repeatability.
    """
    context, root = repair
    path = root / "42161-timestamps.duckdb"
    original = path.read_bytes()
    dense_timestamps.repair_dense_timestamp_caches(context)
    report = context.other_data["dense_42161"]
    assert report == {"first": 1000, "last": 1030, "preserved_rows": 3, "added_rows": 28, "missing_rows": 0}
    backup = Path(context.other_data["dense_timestamp_recovery"][str(path)]["backup"])
    assert backup.read_bytes() == original
    database = BlockTimestampDatabase(42161, path)
    try:
        assert database.get_count() == 31
        assert database.find_gaps() == []
    finally:
        database.close()
    before_repeat = path.read_bytes()
    dense_timestamps.repair_dense_timestamp_caches(context)
    assert path.read_bytes() == before_repeat
    assert len(context.other_data["backup_paths"]) == 1


def test_dense_timestamp_failure_resumes_candidate(repair: tuple[MigrationContext, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the canonical cache intact and reuse durable provider progress.

    A provider interruption after a partial chunk must retain its candidate,
    then a later attempt can finish and publish without a second backup.

    :param repair: Isolated sparse cache and migration context.
    :param monkeypatch: Interrupt the first collector call after committing rows.
    :return: None after a successful resumed repair.
    """
    context, root = repair
    path = root / "42161-timestamps.duckdb"
    original = path.read_bytes()
    collector = dense_timestamps.fetch_block_timestamps_using_hypersync_cached

    def interrupted(_client: object, chain_id: int, _first: int, _last: int, cache_path: Path, **_kwargs: object) -> None:
        """Persist a partial chunk before reporting a provider failure."""
        database = BlockTimestampDatabase(chain_id, cache_path / path.name)
        try:
            database.import_chain_data(chain_id, pd.Series({1001: 1700001001}))
        finally:
            database.close()
        msg = "provider unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(dense_timestamps, "fetch_block_timestamps_using_hypersync_cached", interrupted)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        dense_timestamps.repair_dense_timestamp_caches(context)
    assert path.read_bytes() == original
    entry = context.other_data["dense_timestamp_recovery"][str(path)]
    assert entry["stage_ready"]
    stage = BlockTimestampDatabase(42161, Path(entry["stage"]))
    try:
        assert stage.get_count() == 4
    finally:
        stage.close()
    monkeypatch.setattr(dense_timestamps, "fetch_block_timestamps_using_hypersync_cached", collector)
    dense_timestamps.repair_dense_timestamp_caches(context)
    assert context.other_data["dense_42161"]["added_rows"] == 28


def test_dense_timestamp_runner_preserves_recovery_on_restart(repair: tuple[MigrationContext, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Resume a failed numbered run from its persisted candidate and journal.

    A fresh runner invocation must restore recovery fields rather than silently
    copy the source again and discard downloaded progress.

    :param repair: Isolated sparse cache and deterministic collector.
    :param monkeypatch: Interrupt the first collection and inspect the retry.
    :param tmp_path: Isolated script inventory and pipeline journal.
    :return: None after apply, retry and successful skip.
    """
    context, root = repair
    context.data_dir.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = Path(__file__).parents[2] / "migration/scripts/070-dense-block-timestamp-caches.py"
    shutil.copy2(script, scripts / script.name)
    collector = dense_timestamps.fetch_block_timestamps_using_hypersync_cached
    stages = []

    def interrupted(client: object, chain_id: int, first: int, last: int, cache_path: Path, **_kwargs: object) -> object:
        """Persist one row on failure, then require it on the next attempt."""
        stages.append(cache_path)
        database = BlockTimestampDatabase(chain_id, cache_path / f"{chain_id}-timestamps.duckdb")
        try:
            if len(stages) == 1:
                database.import_chain_data(chain_id, pd.Series({1001: 1700001001}))
            else:
                assert database.get_count() == 4
        finally:
            database.close()
        if len(stages) == 1:
            message = "provider unavailable"
            raise RuntimeError(message)
        return collector(client, chain_id, first, last, cache_path)

    monkeypatch.setattr(dense_timestamps, "fetch_block_timestamps_using_hypersync_cached", interrupted)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        run_pending_migrations(dry_run=False, data_dir=context.data_dir, scripts_dir=scripts)
    assert run_pending_migrations(dry_run=False, data_dir=context.data_dir, scripts_dir=scripts) == [script.name]
    assert stages[0] == stages[1]
    assert len(tuple((root / ".timestamp-recovery").iterdir())) == 1
    assert run_pending_migrations(dry_run=False, data_dir=context.data_dir, scripts_dir=scripts) == []


def test_dense_timestamp_interrupted_backup_can_retry(repair: tuple[MigrationContext, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not journal a partial backup as completed recovery evidence.

    A later attempt starts a fresh backup while preserving the original cache
    and leaving the failed copy available for inspection.

    :param repair: Isolated sparse cache and migration context.
    :param monkeypatch: Interrupt the first independent backup copy.
    :return: None after a successful retry from the unchanged source.
    """
    context, root = repair
    path = root / "42161-timestamps.duckdb"
    original = path.read_bytes()
    copy = dense_timestamps.durable_copy

    def interrupted(source: Path, destination: Path) -> None:
        """Leave an incomplete backup, like a process interrupted during copy."""
        destination.write_bytes(source.read_bytes()[:32])
        message = "copy interrupted"
        raise OSError(message)

    monkeypatch.setattr(dense_timestamps, "durable_copy", interrupted)
    with pytest.raises(OSError, match="copy interrupted"):
        dense_timestamps.repair_dense_timestamp_caches(context)
    assert path.read_bytes() == original
    assert not context.other_data.get("dense_timestamp_recovery")
    monkeypatch.setattr(dense_timestamps, "durable_copy", copy)
    dense_timestamps.repair_dense_timestamp_caches(context)
    assert context.other_data["dense_42161"]["missing_rows"] == 0


def test_dense_timestamp_changed_original_is_rejected(repair: tuple[MigrationContext, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a candidate that changes an existing cached timestamp.

    A provider disagreement must preserve the canonical database for inspection.

    :param repair: Isolated sparse cache and migration context.
    :param monkeypatch: Alter one original row in the candidate.
    :return: None after checking refusal and preservation.
    """
    context, root = repair
    path = root / "42161-timestamps.duckdb"
    original = path.read_bytes()
    collector = dense_timestamps.fetch_block_timestamps_using_hypersync_cached

    def incorrect(client: object, chain_id: int, first: int, last: int, cache_path: Path, **_kwargs: object) -> object:
        """Return dense coverage with one deliberately corrupted timestamp."""
        slicer = collector(client, chain_id, first, last, cache_path)
        slicer.timestamp_db.con.execute("UPDATE block_timestamps SET timestamp=1 WHERE block_number=1010")
        return slicer

    monkeypatch.setattr(dense_timestamps, "fetch_block_timestamps_using_hypersync_cached", incorrect)
    with pytest.raises(ValueError, match="changed an original"):
        dense_timestamps.repair_dense_timestamp_caches(context)
    assert path.read_bytes() == original


@pytest.mark.skipif(not os.environ.get("HYPERSYNC_API_KEY"), reason="Requires supplied HyperSync credentials")
# 2026-11-13: shared CI quota produced 429s; the isolated local read passed.
@pytest.mark.hypersync_retry
def test_dense_arbitrum_live(tmp_path: Path) -> None:
    """Check real Envio headers stay dense despite wide price sampling.

    Use an isolated cache and a short fixed Arbitrum interval. The source is
    the actual configured provider, without replacing requests or responses.

    :param tmp_path: Temporary cache, independent of operator databases.
    :return: None after checking every block and a known timestamp.
    """
    config = configure_hypersync_from_env(42161)
    slicer = fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=42161,
        web3factory=None,
        start_block=483358787,
        end_block=483358987,
        step=100,
        cache_path=tmp_path,
        hypersync_client=config.hypersync_client,
        display_progress=False,
    )
    try:
        assert slicer.timestamp_db.get_count() == 201
        assert slicer.timestamp_db.find_gaps() == []
        assert int(slicer[483358787].replace(tzinfo=datetime.UTC).timestamp()) == 1783930721
    finally:
        slicer.close()
