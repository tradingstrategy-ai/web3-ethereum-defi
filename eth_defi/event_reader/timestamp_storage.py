"""Validate and compact timestamp caches without changing their logical contents.

Native DuckDB ordering improves compression and zonemap pruning. Operational
replacement belongs to numbered migrations; ordinary cache opens never compact.
See https://duckdb.org/docs/stable/guides/performance/indexing.html.
"""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import duckdb
import numpy as np

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def storage_progress(operation: str) -> Iterator[None]:
    """Log a heartbeat while native SQL, copies or validation are running.

    A bounded background thread makes work observable even during a blocking
    DuckDB operation. It is stopped before leaving the context.

    :param operation: Human-readable action and source identifier.
    :return: Context emitting a heartbeat every twenty seconds.
    """
    stopped = threading.Event()

    def heartbeat() -> None:
        """Report the current operation until the context exits."""
        while not stopped.wait(20):
            logger.info("Still working: %s", operation)

    thread = threading.Thread(target=heartbeat, daemon=True)
    logger.info("Starting %s", operation)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()


def file_digest(path: Path) -> str:
    """Hash a file with bounded memory for recovery identity checks.

    Digests identify bytes, while exact ordered comparisons establish logical
    equivalence. These checks serve different purposes.

    :param path: Closed database or recovery file.
    :return: SHA-256 hex digest.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(path: Path) -> None:
    """Persist directory entries after backup creation or atomic replacement.

    Linux directory fsync makes a completed rename durable across a power loss.

    :param path: Directory containing the changed entries.
    :return: ``None`` after synchronisation.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_copy(source: Path, destination: Path) -> None:
    """Create an independent, exclusive recovery copy and persist its bytes.

    Unlike a hard link, this backup cannot be changed by a stale writer on the
    source inode. Existing backup files are never overwritten.

    :param source: Quiescent source file.
    :param destination: New backup pathname.
    :return: ``None`` after the backup and directory entry are durable.
    """
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    shutil.copystat(source, destination)
    sync_directory(destination.parent)


@contextlib.contextmanager
def storage_connection(path: Path, scratch: Path, *, read_only: bool = True) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a bounded direct connection without running cache initialisation.

    Each invocation has an independent spill directory. Sources opened here
    retain their legacy constraints and physical bytes.

    :param path: Database file.
    :param scratch: Parent directory for transient spill files.
    :param read_only: Whether writes must be prohibited.
    :return: Connection closed before its spill directory is removed.
    """
    with tempfile.TemporaryDirectory(dir=scratch, prefix="timestamp-spill-") as spill:
        connection = duckdb.connect(str(path), read_only=read_only, config={"threads": 4, "memory_limit": "2GB", "temp_directory": spill})
        try:
            yield connection
        finally:
            connection.close()


def inspect_timestamp_file(path: Path, scratch: Path) -> dict:
    """Reject unexpected storage objects and report a timestamp file's layout.

    This intentionally fails closed rather than losing unrelated objects when
    rebuilding the single supported table. WAL recovery is handled separately.

    :param path: Canonical cache or isolated recovered snapshot.
    :param scratch: Spill directory parent.
    :return: JSON-safe schema, size and row statistics.
    """
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular timestamp cache: {path}")
    with storage_connection(path, scratch) as con:
        tables = con.execute("SELECT schema_name,table_name FROM duckdb_tables() WHERE NOT internal").fetchall()
        views = con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
        indexes = con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
        sequences = con.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
        macros = con.execute("SELECT function_name FROM duckdb_functions() WHERE NOT internal AND function_type IN ('macro', 'table_macro')").fetchall()
        columns = con.execute("DESCRIBE block_timestamps").fetchall() if tables == [("main", "block_timestamps")] else []
        if tables != [("main", "block_timestamps")] or views or indexes or sequences or macros or [(c[0], c[1]) for c in columns] != [("block_number", "UBIGINT"), ("timestamp", "UINTEGER")]:
            raise ValueError(f"Unexpected timestamp schema or objects: {path}")
        constraints = con.execute("SELECT constraint_type FROM duckdb_constraints()").fetchall()
        if any(c[0] not in {"PRIMARY KEY", "UNIQUE", "NOT NULL"} for c in constraints):
            raise ValueError(f"Unexpected timestamp constraints: {path}")
        rows, first, last = con.execute("SELECT count(*), min(block_number), max(block_number) FROM block_timestamps").fetchone()
        size = con.execute("PRAGMA database_size").fetchone()
        compression = con.execute("SELECT column_name,compression,sum(count) FROM pragma_storage_info('block_timestamps') GROUP BY ALL").fetchall()
        return dict(rows=rows, first=first, last=last, bytes=path.stat().st_size, allocated_bytes=path.stat().st_blocks * 512, free_bytes=size[5] * size[2], live_bytes=size[4] * size[2], constraints=constraints, compression=compression, duckdb_version=duckdb.__version__, storage_tags=con.execute("SELECT tags FROM duckdb_databases() WHERE NOT internal").fetchone()[0])


def validate_timestamp_files(source: Path, candidate: Path, scratch: Path) -> int:
    """Compare every ordered row and reject nulls and duplicate block numbers.

    Fixed-size Arrow readers bound Python memory. DuckDB readers emit requested
    batch sizes except at EOF; strict zip and batch equality also detect length
    differences. Timestamp duplication is allowed, block duplication is not.

    :param source: Original file, opened read-only.
    :param candidate: Fresh closed file, opened independently read-only.
    :param scratch: Spill directory parent.
    :return: Exactly compared row count.
    """
    with storage_progress(f"validate {source.name}"), storage_connection(source, scratch) as left, storage_connection(candidate, scratch) if source != candidate else contextlib.closing(left.cursor()) as right:
        sql = "SELECT block_number,timestamp FROM block_timestamps ORDER BY block_number NULLS FIRST,timestamp NULLS FIRST"
        readers = [con.execute(sql).to_arrow_reader(1_000_000) for con in (left, right)]
        count = 0
        previous = None
        for a, b in zip(*readers, strict=True):
            if not a.equals(b) or any(column.null_count for column in a.columns):
                raise ValueError(f"Timestamp contents differ or contain nulls: {source}")
            numbers = a.column(0).to_numpy()
            if len(numbers):
                if previous == int(numbers[0]) or np.any(numbers[1:] == numbers[:-1]):
                    raise ValueError(f"Duplicate timestamp block numbers: {source}")
                previous = int(numbers[-1])
            count += a.num_rows
        return count


def build_timestamp_candidate(source: Path, candidate: Path, scratch: Path, *, ordered: bool = True) -> dict:
    """Build and exactly validate a fresh unconstrained timestamp database.

    Candidate creation explicitly selects the existing storage compatibility
    floor. Sorting changes physical row order only, preserving both integers.

    :param source: Quiescent read-only source.
    :param candidate: Non-existing destination file.
    :param scratch: Spill directory parent.
    :param ordered: Sort by block number; false is a benchmark control.
    :return: Candidate statistics including validated row count.
    """
    if candidate.exists():
        raise FileExistsError(candidate)
    original = inspect_timestamp_file(source, scratch)
    if shutil.disk_usage(candidate.parent).free < max(original["bytes"] * 3, 64 * 1024**2):
        raise OSError(f"Insufficient scratch space for {source}")
    with storage_progress(f"build {candidate.name}"), storage_connection(source, scratch) as con:
        # SQL file paths are escaped literals, never shell-interpolated commands.
        escaped = str(candidate).replace("'", "''")
        con.execute(f"ATTACH '{escaped}' AS candidate (READ_WRITE, STORAGE_VERSION 'v1.4.0')")
        con.execute("SET wal_autocheckpoint = '1TB'")
        con.execute("CREATE TABLE candidate.block_timestamps AS SELECT block_number,timestamp FROM block_timestamps" + (" ORDER BY block_number" if ordered else ""))
        con.execute("CHECKPOINT candidate")
    reopened = subprocess.run(
        [sys.executable, "-c", "import duckdb,json,sys; c=duckdb.connect(sys.argv[1],read_only=True); print(json.dumps(c.execute('SELECT count(*) FROM block_timestamps').fetchone())); c.close()", str(candidate)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if json.loads(reopened.stdout) != [original["rows"]]:
        raise ValueError(f"Fresh-process reopen changed timestamp count: {candidate}")
    rows = validate_timestamp_files(source, candidate, scratch)
    result = inspect_timestamp_file(candidate, scratch)
    assert result["rows"] == rows == original["rows"]
    return result
