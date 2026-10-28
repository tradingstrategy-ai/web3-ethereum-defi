"""Verified file backups and progress reporting for scanner database maintenance.

These primitives are shared by local migrations and private R2 snapshots. The
caller owns the database connection and must stop other database owners before
checkpointing or copying persistent state.
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


def file_sha256(path: Path) -> str:
    """Hash an evidence file without loading it into memory.

    Progress is visible for production-sized files.

    :param path: Existing immutable file.
    :return: SHA-256 hexadecimal digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream, tqdm(total=path.stat().st_size, unit="B", unit_scale=True, desc=f"Hashing {path.name}") as bar:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
            bar.update(len(chunk))
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict) -> None:
    """Persist a report or backup receipt atomically and durably.

    A failed write cannot replace a completed receipt with partial JSON.

    :param path: Destination path.
    :param value: JSON-serialisable report.
    :return: ``None``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w") as stream:
            json.dump(value, stream, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def observe_database_operation(label: str) -> Iterator[None]:
    """Log a heartbeat while an uninterruptible DuckDB operation runs.

    The heartbeat never uses the database connection and stops on context exit.

    :param label: Operation context shown in logs.
    :return: Context yielding ``None``.
    """
    stopped = threading.Event()

    def heartbeat() -> None:
        """Report progress until the owning operation leaves its context."""
        while not stopped.wait(30):
            logger.info("Still running: %s", label)

    thread = threading.Thread(target=heartbeat, daemon=True)
    logger.info("Starting: %s", label)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
        logger.info("Finished: %s", label)


def copy_database_file(source: Path, destination: Path) -> None:
    """Copy a checkpointed database with progress and durable file contents.

    The caller keeps the source's exclusive connection while this runs.

    :param source: Checkpointed, stable source database.
    :param destination: New snapshot filename.
    :return: ``None`` after fsync.
    """
    with source.open("rb") as incoming, destination.open("xb") as outgoing, tqdm(total=source.stat().st_size, unit="B", unit_scale=True, desc=f"Copying {source.name}") as progress:
        while chunk := incoming.read(8 * 1024 * 1024):
            outgoing.write(chunk)
            progress.update(len(chunk))
        outgoing.flush()
        os.fsync(outgoing.fileno())


def backup_database(connection: duckdb.DuckDBPyConnection, path: Path, backup_dir: Path) -> dict:
    """Create and verify an independent backup before any repair transaction.

    The exclusive read/write connection excludes external DuckDB writers.
    Checkpoint first to include committed WAL contents, retain that connection
    while copying, and abort before migration if verification or fsync fails.

    :param connection: Exclusively held database connection.
    :param path: Target database, with its owner stopped.
    :param backup_dir: Backup directory outside the target path.
    :return: Durable backup receipt containing its path and SHA-256.
    """
    with observe_database_operation(f"Checkpoint before own backup: {path}"):
        connection.execute("CHECKPOINT")
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{path.stem}-{native_datetime_utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex}.duckdb"
    copy_database_file(path, destination)
    source_hash, copied_hash = file_sha256(path), file_sha256(destination)
    if source_hash != copied_hash:
        msg = "Migration backup checksum mismatch; refusing to mutate database"
        raise ValueError(msg)
    receipt = {"database": str(path.resolve()), "backup": str(destination.resolve()), "sha256": copied_hash, "size": destination.stat().st_size, "created_at": native_datetime_utc_now().isoformat()}
    write_json_atomic(destination.with_suffix(".json"), receipt)
    return receipt
