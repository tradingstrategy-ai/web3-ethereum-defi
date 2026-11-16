"""Numbered timestamp compaction with durable per-file recovery evidence.

The runner owns pipeline locks and the journal. This helper additionally
excludes cooperating timestamp connections using the stable cache directory.
"""

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.event_reader.timestamp_lock import timestamp_access_lock
from eth_defi.event_reader.timestamp_storage import build_timestamp_candidate, durable_copy, file_digest, inspect_timestamp_file, storage_connection, storage_progress, sync_directory, validate_timestamp_files
from eth_defi.vault_protocols.migration import MigrationContext

logger = logging.getLogger(__name__)


def _snapshot(source: Path, destination: Path) -> Path:
    """Copy and recover a quiescent database including its WAL in scratch.

    Recovery never touches the original; a read-write checkpoint happens only
    on the isolated paired copy.

    :param source: Quiescent canonical file or recovery backup.
    :param destination: Non-existing scratch file.
    :return: Recovered snapshot pathname.
    """
    shutil.copy2(source, destination)
    wal = Path(str(source) + ".wal")
    if wal.exists():
        shutil.copy2(wal, Path(str(destination) + ".wal"))
        with storage_connection(destination, destination.parent, read_only=False) as con:
            con.execute("CHECKPOINT")
    return destination


def compact_timestamp_caches(context: MigrationContext, *, chain_ids: tuple[int, ...] | None = None) -> None:
    """Preview or publish lossless per-chain sorted timestamp caches.

    All sources are preflighted before publication. Each replacement retains
    independent database/WAL backups and journals intent before atomic rename.
    Retry reconciles byte identities; cache users must remain stopped throughout
    an interrupted attempt. No metadata or prices are loaded or changed.

    :param context: Runner context holding lifecycle and apply writer locks.
    :param chain_ids: Explicit manual scope, or all canonical caches.
    :return: ``None`` after preview or fully validated publication.
    """
    if not os.environ.get("TIMESTAMP_CACHE") and (os.environ.get("PIPELINE_DATA_DIR") or context.data_dir.resolve() != (DEFAULT_TIMESTAMP_CACHE_FOLDER.parent / "vaults").resolve()):
        raise ValueError("PIPELINE_DATA_DIR requires explicit TIMESTAMP_CACHE for isolated timestamp maintenance")
    root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().absolute()
    if root.is_symlink() or not root.is_dir() or root.resolve() == context.data_dir.resolve():
        raise ValueError(f"Unsafe timestamp directory: {root}")
    paths = sorted(p for p in root.glob("*-timestamps.duckdb") if re.fullmatch(r"[0-9]+-timestamps\.duckdb", p.name))
    if chain_ids is not None:
        if not chain_ids or any(chain <= 0 for chain in chain_ids) or len(set(chain_ids)) != len(chain_ids):
            raise ValueError("Select distinct positive timestamp chain IDs")
        selected = {root / f"{chain}-timestamps.duckdb" for chain in chain_ids}
        if not selected.issubset(paths):
            raise ValueError(f"Missing selected timestamp caches: {selected.difference(paths)}")
        paths = [path for path in paths if path in selected]
    if not paths:
        raise ValueError(f"No timestamp databases found in {root}")
    sizes = [p.stat().st_size + (Path(str(p) + ".wal").stat().st_size if Path(str(p) + ".wal").exists() else 0) for p in paths]
    required_space = sum(sizes) + 4 * max(sizes) + 64 * 1024**2
    if shutil.disk_usage(root).free < required_space:
        raise OSError(f"Timestamp maintenance needs {required_space:,} free bytes including backups and spill")
    for leftover in root.glob(".timestamp-compact-*"):
        logger.warning("Retained scratch directory from an earlier attempt: %s; inspect before removal", leftover)
    records = context.other_data.get("storage_recovery", {})
    inventory = [str(p) for p in paths]
    if records and set(records) != set(inventory):
        raise ValueError("Timestamp inventory changed since interrupted migration")
    with timestamp_access_lock(root, exclusive=True), tempfile.TemporaryDirectory(dir=root, prefix=".timestamp-compact-") as temporary, storage_progress("timestamp migration"):
        scratch = Path(temporary)
        locked_inventory = sorted(p for p in root.glob("*-timestamps.duckdb") if re.fullmatch(r"[0-9]+-timestamps\.duckdb", p.name))
        if chain_ids is not None:
            locked_inventory = [path for path in locked_inventory if path in selected]
        if locked_inventory != paths:
            raise ValueError("Timestamp inventory changed before exclusive access")
        # Validate every chain before publishing the first chain. Snapshots also
        # allow a preview of a quiescent WAL without changing its source.
        for index, path in enumerate(paths):
            if path.is_symlink():
                raise ValueError(f"Symlink timestamp cache: {path}")
            snapshot = _snapshot(path, scratch / f"preflight-{index}.duckdb")
            inspect_timestamp_file(snapshot, scratch)
            validate_timestamp_files(snapshot, snapshot, scratch)
            snapshot.unlink()
        if not records:
            records = {str(path): {"source_hash": file_digest(path), "wal_hash": file_digest(Path(str(path) + ".wal")) if Path(str(path) + ".wal").exists() else None, "phase": "pending"} for path in paths}
            context.report(storage_recovery=records)
        # Validate retained evidence even when a prior rename already succeeded.
        for entry in records.values():
            if entry.get("backup"):
                saved = Path(entry["backup"])
                saved_wal = Path(str(saved) + ".wal")
                if not saved.is_file() or file_digest(saved) != entry["backup_hash"]:
                    raise ValueError(f"Recovery backup changed: {saved}")
                if (file_digest(saved_wal) if saved_wal.exists() else None) != entry.get("backup_wal_hash"):
                    raise ValueError(f"Recovery WAL changed: {saved_wal}")
        reports = []
        for index, path in enumerate(paths):
            with tempfile.TemporaryDirectory(dir=temporary, prefix=f"chain-{index}-") as chain_scratch:
                scratch = Path(chain_scratch)
                entry = records[str(path)]
                current = file_digest(path)
                if entry.get("candidate_hash") == current and not Path(str(path) + ".wal").exists():
                    if not context.dry_run:
                        context.record_modified_file(path)
                    entry["phase"] = "published"
                    context.report(storage_recovery=records)
                    continue
                wal = Path(str(path) + ".wal")
                if entry["phase"] == "published":
                    raise ValueError(f"Published cache changed after interruption: {path}")
                backup = Path(entry["backup"]) if entry.get("backup") else None
                current_wal_hash = file_digest(wal) if wal.exists() else None
                if current != entry["source_hash"] or current_wal_hash != entry["wal_hash"]:
                    # A checkpoint may finish before its journal update. Prove
                    # full equivalence to the independent paired backup before
                    # accepting either changed database bytes or a removed WAL.
                    if backup is None or not backup.is_file():
                        raise ValueError(f"Timestamp source or WAL changed: {path}")
                    recovered = _snapshot(backup, scratch / f"reconcile-{index}.duckdb")
                    current_snapshot = _snapshot(path, scratch / f"current-{index}.duckdb")
                    validate_timestamp_files(recovered, current_snapshot, scratch)
                    entry.update(source_hash=current, wal_hash=current_wal_hash)
                    context.report(storage_recovery=records)
                source = _snapshot(path, scratch / f"source-{index}.duckdb")
                before = inspect_timestamp_file(source, scratch)
                candidate = scratch / f"candidate-{index}.duckdb"
                after = build_timestamp_candidate(source, candidate, scratch)
                reports.append(dict(path=str(path), before=before, after=after))
                if after["bytes"] >= path.stat().st_size + (wal.stat().st_size if wal.exists() else 0):
                    entry["phase"] = "unchanged"
                    context.report(storage_recovery=records, timestamp_files=reports)
                    continue
                if context.dry_run:
                    continue
                if backup is None:
                    recovery = root / ".timestamp-recovery"
                    recovery.mkdir(exist_ok=True)
                    if recovery.is_symlink():
                        raise ValueError("Recovery directory must not be a symlink")
                    backup_dir = Path(tempfile.mkdtemp(dir=recovery, prefix=f"{context.script_name}-{index}-"))
                    sync_directory(root)
                    sync_directory(recovery)
                    backup = backup_dir / path.name
                    durable_copy(path, backup)
                    if file_digest(backup) != entry["source_hash"]:
                        raise ValueError(f"Backup does not match frozen source: {path}")
                    context.record_modified_file(backup, backup=True)
                    if wal.exists():
                        durable_copy(wal, Path(str(backup) + ".wal"))
                        context.record_modified_file(Path(str(backup) + ".wal"), backup=True)
                    backup_wal = Path(str(backup) + ".wal")
                    if (file_digest(backup_wal) if backup_wal.exists() else None) != entry["wal_hash"]:
                        raise ValueError(f"Backup WAL does not match frozen source: {path}")
                    entry.update(backup=str(backup), backup_hash=entry["source_hash"], backup_wal_hash=entry["wal_hash"], phase="backed_up")
                    context.report(storage_recovery=records)
                backup_wal = Path(str(backup) + ".wal")
                if (file_digest(backup_wal) if backup_wal.exists() else None) != entry.get("backup_wal_hash"):
                    raise ValueError(f"Recovery WAL changed: {backup_wal}")
                if file_digest(backup) != entry["backup_hash"]:
                    raise ValueError(f"Recovery backup changed: {backup}")
                if wal.exists():
                    with storage_connection(path, scratch, read_only=False) as con:
                        con.execute("CHECKPOINT")
                    context.record_modified_file(path)
                    validate_timestamp_files(source, path, scratch)
                    entry.update(source_hash=file_digest(path), wal_hash=None)
                    context.report(storage_recovery=records)
                if wal.exists() or file_digest(path) != entry["source_hash"]:
                    raise ValueError(f"Timestamp source changed before replacement: {path}")
                stat = path.stat()
                shutil.copystat(path, candidate)
                os.chown(candidate, stat.st_uid, stat.st_gid)
                with candidate.open("rb") as stream:
                    os.fsync(stream.fileno())
                entry.update(candidate_hash=file_digest(candidate), rows=after["rows"], phase="prepared")
                context.report(storage_recovery=records)
                os.replace(candidate, path)
                sync_directory(root)
                context.record_modified_file(path)
                validate_timestamp_files(source, path, scratch)
                entry["phase"] = "published"
                context.report(storage_recovery=records, timestamp_files=reports)
        context.report(storage_recovery=records, timestamp_files=reports)
