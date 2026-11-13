"""Fill historical timestamp gaps through the existing dense HyperSync reader.

Only timestamp databases are changed. The numbered runner owns pipeline guards;
the cache-directory lock excludes other timestamp consumers during maintenance.
"""

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from tqdm_loggable.auto import tqdm

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.event_reader.timestamp_lock import timestamp_access_lock
from eth_defi.event_reader.timestamp_storage import build_timestamp_candidate, durable_copy, file_digest, storage_connection, storage_progress, sync_directory
from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.server import is_hypersync_supported_chain
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.vault_protocols.migration import MigrationContext

logger = logging.getLogger(__name__)


def validate_timestamp_extension(source: Path, repaired: Path, scratch: Path) -> int:
    """Prove every original block and timestamp survives an additive repair.

    Merge ordered Arrow batches with bounded Python memory. Inserted blocks are
    allowed, but a missing original block or changed timestamp aborts publication.

    :param source: Unchanged original cache, opened read-only.
    :param repaired: Closed, densely repaired candidate.
    :param scratch: Directory for bounded DuckDB sort spill.
    :return: Number of original rows compared exactly.
    """
    with storage_progress(f"preserve {source.name}"), storage_connection(source, scratch) as before, storage_connection(repaired, scratch) as after:
        sql = "SELECT block_number,timestamp FROM block_timestamps ORDER BY block_number"
        original = before.execute(sql).to_arrow_reader(1_000_000)
        candidate = iter(after.execute(sql).to_arrow_reader(1_000_000))
        batch = next(candidate, None)
        compared = 0
        for old in original:
            numbers = old.column(0).to_numpy()
            stamps = old.column(1).to_numpy()
            offset = 0
            while offset < len(numbers):
                while batch is not None and batch.column(0)[-1].as_py() < int(numbers[offset]):
                    batch = next(candidate, None)
                if batch is None:
                    msg = "Timestamp repair lost original blocks"
                    raise ValueError(msg)
                new_numbers = batch.column(0).to_numpy()
                new_stamps = batch.column(1).to_numpy()
                stop = int(np.searchsorted(numbers, new_numbers[-1], side="right"))
                indices = np.searchsorted(new_numbers, numbers[offset:stop])
                if np.any(indices >= len(new_numbers)) or not np.array_equal(new_numbers[indices], numbers[offset:stop]) or not np.array_equal(new_stamps[indices], stamps[offset:stop]):
                    msg = "Timestamp repair changed an original block or timestamp"
                    raise ValueError(msg)
                compared += stop - offset
                offset = stop
        return compared


def repair_dense_timestamp_caches(context: MigrationContext, *, chain_ids: tuple[int, ...] | None = None) -> None:  # noqa: PLR0914 -- per-file recovery evidence stays together.
    """Fill existing HyperSync-supported cache envelopes without losing rows.

    Preview only reports missing counts. Apply keeps independent backups, fills
    a resumable candidate and validates density and original rows before atomic
    replacement. Unsupported RPC-only chains are reported and left untouched.
    No new chain files or genesis/head ranges are invented.

    :param context: Guarded numbered migration attempt.
    :param chain_ids: Local manual selection, or every canonical existing cache.
    :return: None after reporting preview or validated publication.
    """
    root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().absolute()
    if not os.environ.get("TIMESTAMP_CACHE") and context.data_dir.resolve() != (DEFAULT_TIMESTAMP_CACHE_FOLDER.parent / "vaults").resolve():
        msg = "Isolated timestamp maintenance requires explicit TIMESTAMP_CACHE"
        raise ValueError(msg)
    if root.is_symlink() or not root.is_dir() or root.resolve() == context.data_dir.resolve():
        raise ValueError(f"Unsafe timestamp directory: {root}")
    paths = sorted(p for p in root.glob("*-timestamps.duckdb") if re.fullmatch(r"[0-9]+-timestamps\.duckdb", p.name))
    if chain_ids is not None:
        selected = {root / f"{chain}-timestamps.duckdb" for chain in chain_ids}
        if not chain_ids or len(selected) != len(chain_ids) or any(chain <= 0 for chain in chain_ids) or not selected.issubset(paths):
            msg = "Select distinct positive chain IDs with existing caches"
            raise ValueError(msg)
        paths = [p for p in paths if p in selected]
    records = context.other_data.get("dense_timestamp_recovery", {})
    with timestamp_access_lock(root, exclusive=True), storage_progress("dense timestamp migration"):
        for path in tqdm(paths, desc="Dense timestamp caches"):
            chain_id = int(path.name.split("-")[0])
            if path.is_symlink() or Path(str(path) + ".wal").exists():
                raise ValueError(f"Close/checkpoint timestamp cache before maintenance: {path}")
            if not is_hypersync_supported_chain(chain_id):
                logger.warning("Chain %s has no HyperSync endpoint; RPC-only cache left unchanged", chain_id)
                continue
            with storage_connection(path, root) as connection:
                rows, first, last = connection.execute("SELECT count(*),min(block_number),max(block_number) FROM block_timestamps").fetchone()
            missing = last - first + 1 - rows if rows else 0
            logger.info("Chain %s: %s cached rows, %s missing inside %s-%s", chain_id, rows, missing, first, last)
            if missing < 0:
                raise ValueError(f"Duplicate timestamp rows in {path}")
            if not missing:
                continue
            if context.dry_run:
                context.report(**{f"dense_{chain_id}": {"first": first, "last": last, "existing_rows": rows, "missing_rows": missing}})
                continue
            entry = records.get(str(path))
            if entry is None:
                if shutil.disk_usage(root).free < path.stat().st_size * 4 + 64 * 1024**2:
                    raise OSError(f"Insufficient space for timestamp backup, candidate and validation: {path}")
                recovery = root / ".timestamp-recovery"
                recovery.mkdir(exist_ok=True)
                if recovery.is_symlink():
                    msg = "Recovery directory must not be a symlink"
                    raise ValueError(msg)
                folder = Path(tempfile.mkdtemp(dir=recovery, prefix=f"{context.script_name}-{chain_id}-"))
                entry = {"source_hash": file_digest(path), "backup": str(folder / "original.duckdb"), "stage": str(folder / path.name), "stage_ready": False}
                durable_copy(path, Path(entry["backup"]))
                context.record_modified_file(Path(entry["backup"]), backup=True)
                # An interrupted copy is not resumable evidence. Only publish
                # the recovery entry after its independent backup is durable.
                records[str(path)] = entry
                context.report(dense_timestamp_recovery=records)
            if file_digest(path) != entry["source_hash"] or file_digest(Path(entry["backup"])) != entry["source_hash"]:
                raise ValueError(f"Timestamp source or recovery backup changed: {path}")
            stage = Path(entry["stage"])
            if not entry["stage_ready"]:
                stage.unlink(missing_ok=True)
                Path(str(stage) + ".wal").unlink(missing_ok=True)
                build_timestamp_candidate(path, stage, stage.parent)
                entry["stage_ready"] = True
                context.report(dense_timestamp_recovery=records)
            config = configure_hypersync_from_env(chain_id)
            if config.hypersync_client is None:
                raise ValueError(f"Dense timestamp repair requires HyperSync for chain {chain_id}")
            # Bulk repair pages amortise a low provider request quota while
            # retaining bounded memory and durable million-header checkpoints.
            slicer = fetch_block_timestamps_using_hypersync_cached(config.hypersync_client, chain_id, first, last, cache_path=stage.parent, chunk_size=1_000_000)
            try:
                actual = slicer.timestamp_db.get_count()
                if actual != last - first + 1 or slicer.timestamp_db.find_gaps():
                    raise ValueError(f"Dense timestamp repair remains incomplete for chain {chain_id}")
            finally:
                slicer.close()
            # Publication moves only the main file. Explicitly recover and
            # checkpoint any staged WAL before validating the closed database.
            with storage_connection(stage, stage.parent, read_only=False) as connection:
                connection.execute("CHECKPOINT")
                persisted = connection.execute("SELECT count(*) FROM block_timestamps").fetchone()[0]
            if Path(str(stage) + ".wal").exists() or persisted != actual:
                raise ValueError(f"Timestamp candidate is not fully checkpointed: {stage}")
            preserved = validate_timestamp_extension(path, stage, stage.parent)
            if preserved != rows:
                raise ValueError(f"Timestamp preservation count changed for chain {chain_id}")
            stat = path.stat()
            os.chmod(stage, stat.st_mode)
            os.chown(stage, stat.st_uid, stat.st_gid)
            with stage.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(stage, path)
            sync_directory(root)
            context.record_modified_file(path)
            entry["published"] = True
            context.report(dense_timestamp_recovery=records, **{f"dense_{chain_id}": {"first": first, "last": last, "preserved_rows": preserved, "added_rows": actual - rows, "missing_rows": 0}})
