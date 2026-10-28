"""Verified snapshots and recoverable resets of scanner request counters.

The caller holds the scanner's pipeline lock for this entire operation. DuckDB
file locking provides a second check against writers outside that convention.
Legacy accounting schemas stay unchanged so a scanner rollback remains safe.
See :mod:`eth_defi.provider.rpcdb` for the physical-attempt accounting contract.
"""

import datetime
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import duckdb

from eth_defi.compat import native_datetime_utc_now
from eth_defi.provider.rpcdb import ZERO_CALL_MARKER
from eth_defi.version_info import VersionInfo

logger = logging.getLogger(__name__)

#: Only these tables contain counters reset by this utility.
COUNTER_TABLES = ("vault_rpc_api_calls", "vault_rpc_api_errors", "vault_rpc_operation_calls")

#: Durable reset receipts are deliberately preserved across subsequent resets.
RESET_TABLE = "vault_rpc_counter_resets"


def _quote_identifier(value: str) -> str:
    """Quote a database-owned identifier for a generated inspection query.

    Identifiers come from DuckDB's catalogue, never from operator SQL.

    :param value: Table or column identifier.
    :return: Escaped SQL identifier.
    """
    return '"' + value.replace('"', '""') + '"'


def _file_digest(path: Path) -> str:
    """Stream a file checksum for snapshot and conflict verification.

    Maintenance checkpoints the source before its initial copy, then checks
    the same bytes again after reopening it exclusively but before any write.
    This helper hashes only the main file; the caller separately rejects WAL
    presence because a main-file checksum cannot detect uncheckpointed rows.

    :param path: Existing file.
    :return: SHA-256 hex digest.
    """
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch_protected_state_digests(paths: tuple[Path, ...]) -> dict[str, str]:
    """Hash protected pipeline state without reading it into memory.

    Chunk progress keeps large Parquet and timestamp-cache checks observable.
    No file is opened for writing or interpreted as application state.

    :param paths: Explicit critical files selected by the maintenance caller.
    :return: Absolute filenames to SHA-256 digests.
    """
    result = {}
    for path in paths:
        logger.info("Verifying protected state %s", path)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                logger.info("Protected state %s: %d bytes verified", path.name, size)
        result[str(path.resolve())] = digest.hexdigest()
    return result


def _sync_directory(path: Path) -> None:
    """Make newly published maintenance filenames durable.

    This complements fsync on the backup and manifest themselves.

    :param path: Existing containing directory.
    :return: None.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_private_json(path: Path, data: dict) -> None:
    """Exclusively create and fsync a private receipt.

    Existing receipts are never overwritten, including on a timestamp collision.

    :param path: New output filename.
    :param data: JSON-serialisable receipt.
    :return: None.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    _sync_directory(path.parent)


def fetch_counter_inventory(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Inspect every table, retaining canonical content digests and aggregates.

    Digests include all rows, including errors, without exposing messages in
    operator output. Ordering makes the digest independent of storage layout.
    The returned inventory is suitable for source/backup equality checks.

    :param connection: Open connection owned by the maintenance operation.
    :return: Table schemas, counts, digests and counter summaries.
    """
    result = {}
    tables = connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
    for (table,) in tables:
        quoted = _quote_identifier(table)
        schema = connection.execute(f"DESCRIBE {quoted}").fetchall()
        digest = hashlib.sha256()
        count = 0
        cursor = connection.execute(f"SELECT * FROM {quoted} ORDER BY ALL")
        while rows := cursor.fetchmany(10_000):
            for row in rows:
                digest.update(json.dumps(row, default=str, separators=(",", ":"), ensure_ascii=True).encode())
                digest.update(b"\n")
            count += len(rows)
            logger.info("Verified counter table %s: %d rows", table, count)
        entry = {"schema": [list(row) for row in schema], "rows": count, "sha256": digest.hexdigest()}
        if table == "vault_rpc_api_calls":
            entry["calls"] = int(connection.execute(f"SELECT coalesce(sum(call_count),0) FROM {quoted}").fetchone()[0])
            entry["groups"] = [list(row) for row in connection.execute(f"SELECT chain, phase, api_call, rpc_provider_domain, sum(call_count)::BIGINT FROM {quoted} GROUP BY ALL ORDER BY ALL").fetchall()]
        elif table == "vault_rpc_api_errors":
            entry["errors"] = int(connection.execute(f"SELECT coalesce(sum(error_count),0) FROM {quoted}").fetchone()[0])
        if table in COUNTER_TABLES:
            minimum, maximum, cycle = connection.execute(f"SELECT min(cycle_started),max(cycle_started),coalesce(max(cycle_number),0) FROM {quoted}").fetchone()
            entry.update(first_date=str(minimum) if minimum else None, last_date=str(maximum) if maximum else None, max_cycle=int(cycle))
        result[table] = entry
    return result


def _fetch_committed_reset(connection: duckdb.DuckDBPyConnection, reset_id: str) -> dict | None:
    """Read the in-database authoritative reset receipt, if present.

    This check precedes backup and deletion on every retry.

    :param connection: Exclusively opened source connection.
    :param reset_id: Stable operator-supplied operation identity.
    :return: Original manifest or None before commit.
    """
    exists = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=? AND table_schema='main'", [RESET_TABLE]).fetchone()[0]
    if not exists:
        return None
    row = connection.execute(f"SELECT manifest FROM {RESET_TABLE} WHERE reset_id=?", [reset_id]).fetchone()
    return json.loads(row[0]) if row else None


def snapshot_and_reset_rpc_counters(database_path: Path, backup_dir: Path, reset_id: str | None = None, now: datetime.datetime | None = None, protected_paths: tuple[Path, ...] = ()) -> dict:
    """Create a verified dated snapshot and optionally reset physical counters.

    Hold ``wait_other_writers(PIPELINE_DATA_DIR / 'scan-pipeline')`` throughout.
    With no reset ID this only snapshots counters. With an ID, a verified
    backup precedes the transactional reset. Repeating a committed ID verifies
    its backup and returns its receipt without clearing newly accumulated calls.
    No other pipeline files are touched. DuckDB's locking semantics are described
    at https://duckdb.org/docs/stable/connect/concurrency.html.

    The maintenance script selects protected files and owns the pipeline lock;
    this helper owns only the accounting database. Protected hashes prove those
    files did not change during maintenance, but are not backups of their data.
    The dated database copy, pre-reset manifest and transactional reset receipt
    serve different recovery points: deletion is allowed only after the first
    two are verified and durable, and the receipt makes a committed reset
    recoverable even if writing its final JSON acknowledgement fails.

    :param database_path: Existing accounting database, never created implicitly.
    :param backup_dir: Private non-rotating snapshot directory.
    :param reset_id: Stable identity for an intentional reset, or None for snapshot.
    :param now: Naive UTC timestamp; defaults to the current UTC time.
    :param protected_paths: Critical files whose hashes must remain unchanged.
    :return: Verified manifest, including backup path and reset status.
    """
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if reset_id is not None and (not reset_id.strip() or len(reset_id) > 200):
        raise ValueError("A non-empty stable reset ID of at most 200 characters is required")
    now = now or native_datetime_utc_now()
    if now.tzinfo is not None:
        raise ValueError("Maintenance timestamps must be naive UTC")
    connection = duckdb.connect(str(database_path))
    try:
        # Check the stable operation identity before taking another snapshot or
        # deleting anything. A retry after COMMIT must preserve requests recorded
        # since that reset, even if the external completion receipt is missing.
        committed = _fetch_committed_reset(connection, reset_id) if reset_id is not None else None
        if committed is not None:
            backup = Path(committed["backup_path"])
            if _file_digest(backup) != committed["backup_sha256"]:
                raise RuntimeError("Committed reset backup checksum mismatch; counters were not reset again")
            committed["reset_committed"] = True
            completion_path = backup.with_suffix(".completed.json")
            if not completion_path.exists():
                _write_private_json(completion_path, committed)
            return committed
        protected_digests = fetch_protected_state_digests(protected_paths)
        # A byte copy is useful only when all committed rows live in the main
        # file. Close the checkpointed connection before copying it; the later
        # transaction rechecks this snapshot because DuckDB's lock is released.
        connection.execute("CHECKPOINT")
        inventory = fetch_counter_inventory(connection)
    finally:
        connection.close()
    wal_path = Path(str(database_path) + ".wal")
    if wal_path.exists():
        raise RuntimeError("Accounting WAL remains after checkpoint/close; refusing backup")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    purpose = "before-rpc-reduction" if reset_id else "snapshot"
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    backup_path = backup_dir / f"rpc-tracking-{purpose}-{stamp}.duckdb"
    manifest_path = backup_path.with_suffix(".json")
    source_hash = _file_digest(database_path)
    source_size = database_path.stat().st_size
    logger.info("Copying checkpointed counter database to %s", backup_path)
    # Counter errors can contain private provider details. Exclusive creation
    # with owner-only permissions protects that evidence and refuses timestamp
    # collisions instead of replacing an operator's earlier recovery point.
    fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target, database_path.open("rb") as source:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    backup_hash = _file_digest(backup_path)
    if source_hash != backup_hash:
        raise RuntimeError("Source changed during counter backup; reset aborted")
    # Byte equality alone cannot prove that DuckDB can reopen the backup. Also
    # compare schemas and canonical row digests, including non-counter tables,
    # before allowing any destructive step against the source database.
    with duckdb.connect(str(backup_path), read_only=True) as backup:
        backup_inventory = fetch_counter_inventory(backup)
    if inventory != backup_inventory:
        raise RuntimeError("Counter backup content mismatch; reset aborted")
    # Reuse the image's existing build stamp rather than asking operators to
    # duplicate it in maintenance-specific environment variables. This identifies
    # the maintenance image, which can differ from the stopped scanner: its
    # deployment boundary still comes from scanner logs, not this receipt.
    # Cadence configuration is allowlisted; RPC URLs and secrets stay out.
    manifest = {
        "version": 1,
        "reset_id": reset_id,
        "created_at": now.isoformat(),
        "source_path": str(database_path.resolve()),
        "backup_path": str(backup_path.resolve()),
        "backup_sha256": backup_hash,
        "source_size": source_size,
        "duckdb_version": duckdb.__version__,
        "maintenance_version": VersionInfo.read_docker_version().as_dict(),
        "measurement_version": 1,
        "rpc_optimisations": "always_enabled",
        "configuration": {key: os.environ.get(key) for key in ("SCAN_CYCLES", "DEFAULT_CYCLE", "LOOP_INTERVAL_SECONDS")},
        "tables": inventory,
        "protected_state_sha256": protected_digests,
        "reset_committed": False,
    }
    # Publishing and syncing the manifest also syncs the backup directory.
    # A crash before this point leaves counters intact; a crash afterwards has
    # a verified dated recovery copy even if the reset transaction never starts.
    _write_private_json(manifest_path, manifest)
    if reset_id is None:
        return manifest

    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("BEGIN TRANSACTION")
        try:
            # File exclusivity is restored here. A writer may have bypassed the
            # pipeline lock while the source was closed for its byte snapshot.
            # Exact checkpointed bytes already cover every table and row. A
            # second full SQL inventory adds no evidence after this checksum
            # and WAL check, and can make maintenance unnecessarily expensive.
            if wal_path.exists() or _file_digest(database_path) != source_hash:
                raise RuntimeError("Counters changed after backup verification; reset aborted")
            maximum_cycle = max((entry.get("max_cycle", 0) for entry in inventory.values()), default=0)
            # Reset request measurements, not scheduling history. The zero-call
            # marker preserves MAX(cycle_number) for both old and new allocators;
            # removing it would make subsequent scans reuse old cycle identities.
            for table in COUNTER_TABLES:
                if table in inventory:
                    connection.execute(f"DELETE FROM {_quote_identifier(table)}")
            connection.execute("INSERT INTO vault_rpc_api_calls VALUES (0, 'counter_reset', ?, ?, ?, ?, 0, 0)", [ZERO_CALL_MARKER, now.date(), maximum_cycle, ZERO_CALL_MARKER])
            # Store the authoritative receipt in the deletion transaction. Do
            # not use ART-backed uniqueness constraints here (DuckDB/Python 3.14
            # ingestion issue); exclusive maintenance and the earlier ID check
            # enforce the operation's idempotency at the application boundary.
            connection.execute(f"CREATE TABLE IF NOT EXISTS {RESET_TABLE} (reset_id VARCHAR, committed_at TIMESTAMP, old_max_cycle BIGINT, manifest VARCHAR)")
            connection.execute(f"INSERT INTO {RESET_TABLE} VALUES (?, ?, ?, ?)", [reset_id, now, maximum_cycle, json.dumps(manifest, sort_keys=True)])
            # Compare once immediately before commit. Rehashing large price and
            # timestamp files before deletion as well would repeat the same
            # check; this transaction rolls deletion back if the final check fails.
            if fetch_protected_state_digests(protected_paths) != protected_digests:
                raise RuntimeError("Protected pipeline state changed before reset commit; reset aborted")
            connection.execute("COMMIT")
        except BaseException:
            # KeyboardInterrupt and cancellation must roll back too. After an
            # uncertain commit outcome, the same reset ID is the recovery key;
            # inventing a new one could clear freshly collected measurements.
            try:
                connection.execute("ROLLBACK")
            except duckdb.TransactionException:
                logger.warning("Reset transaction is already closed; retain the stable reset ID for recovery")
            raise
        connection.execute("CHECKPOINT")
        if connection.execute("SELECT count(*), coalesce(sum(call_count),0) FROM vault_rpc_api_calls").fetchone() != (1, 0):
            raise RuntimeError("Reset committed but verification failed; reuse the same reset ID for recovery")
        if connection.execute("SELECT count(*) FROM vault_rpc_api_errors").fetchone()[0] != 0:
            raise RuntimeError("Reset committed but errors remain; reuse the same reset ID for recovery")
    finally:
        connection.close()
    # This file is an operator acknowledgement, not the commit authority. The
    # in-database receipt above repairs it on retry after a crash or disk error.
    manifest["reset_committed"] = True
    _write_private_json(backup_path.with_suffix(".completed.json"), manifest)
    logger.info("Counter reset committed; next scanner cycle is %d", maximum_cycle + 1)
    return manifest


#: Compatibility name for existing maintenance callers; new callers should use
#: the name that makes the optional destructive reset explicit.
backup_rpc_counters = snapshot_and_reset_rpc_counters
