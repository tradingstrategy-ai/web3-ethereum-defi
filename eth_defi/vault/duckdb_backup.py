"""Private, consistent DuckDB backups with a durable per-database 48-hour gate.

A conditional R2 state write claims each attempt before touching database
contents. Immutable attempt receipts survive local-state loss and failed runs.
Only stopped owners can be snapshotted: an exclusive DuckDB connection must
succeed before checkpoint and copy. R2 conditional writes are documented at
https://developers.cloudflare.com/r2/api/s3/api/.
"""

import datetime
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from tqdm_loggable.auto import tqdm

from eth_defi.cloudflare_r2 import create_r2_client
from eth_defi.compat import native_datetime_utc_now
from eth_defi.core3.constants import resolve_core3_database_path
from eth_defi.feed.database import resolve_feed_database_path
from eth_defi.provider.rpcdb import resolve_rpc_tracking_database_path
from eth_defi.vault.backup import copy_database_file, file_sha256, observe_database_operation
from eth_defi.xerberus.constants import resolve_xerberus_database_path

logger = logging.getLogger(__name__)
MINIMUM_BACKUP_INTERVAL = datetime.timedelta(days=2)


@dataclass(slots=True)
class DuckDBBackup:
    """Explicitly registered database with a stable logical identity.

    Nested caches are included only by explicitly naming their owner and path.
    """

    #: Stable identity used for remote cadence and immutable snapshots.
    name: str
    #: Operator-owned stopped database file.
    path: Path


class DuckDBBackupError(RuntimeError):
    """Report an incomplete backup batch without hiding successful members."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses
        super().__init__("DuckDB backup failures: " + ", ".join(name for name, status in statuses.items() if status == "failed"))


def create_private_backup_client() -> Any:
    """Create the private data client from the scanner's environment settings.

    Prefer ``R2_DATA_*`` credentials, with the existing ``R2_VAULT_METADATA_*``
    variables as fallbacks. Bucket selection remains explicit at each caller.
    See https://developers.cloudflare.com/r2/api/s3/api/.

    :return: Authenticated S3-compatible client; credentials are never logged.
    """
    variables = {
        "endpoint_url": ("R2_DATA_ENDPOINT_URL", "R2_VAULT_METADATA_ENDPOINT_URL"),
        "access_key_id": ("R2_DATA_ACCESS_KEY_ID", "R2_VAULT_METADATA_ACCESS_KEY_ID"),
        "secret_access_key": ("R2_DATA_SECRET_ACCESS_KEY", "R2_VAULT_METADATA_SECRET_ACCESS_KEY"),
    }
    settings = {name: os.environ.get(primary) or os.environ.get(fallback) for name, (primary, fallback) in variables.items()}
    missing = [" or ".join(variables[name]) for name, value in settings.items() if not value]
    if missing:
        msg = "Missing private R2 configuration: " + ", ".join(missing)
        raise ValueError(msg)
    return create_r2_client(**settings)


def backup_key_root() -> str:
    """Resolve a stable namespace without resetting cadence on prefix changes.

    Prefixed deployments must explicitly select a persistent logical namespace,
    preventing staging from sharing production gates or compatibility objects.

    :return: Remote scheduling and immutable snapshot prefix.
    """
    namespace = os.environ.get("DUCKDB_BACKUP_NAMESPACE", "")
    if os.environ.get("UPLOAD_PREFIX") and not namespace:
        msg = "Prefixed DuckDB exports require a stable DUCKDB_BACKUP_NAMESPACE"
        raise ValueError(msg)
    if namespace and (not namespace.replace("-", "_").isidentifier()):
        msg = "Invalid DuckDB backup namespace"
        raise ValueError(msg)
    return "duckdb-backups/" + (namespace + "/" if namespace else "")


def database_registry(base_path: Path, existing_paths: list[Path]) -> list[DuckDBBackup]:
    """Resolve the scanner database allowlist and configured path overrides.

    Existing export paths supply canonical risk, settlement and currency paths.
    Unknown files are logged for an operator to register, never bulk uploaded.

    :param base_path: Active pipeline state directory.
    :param existing_paths: Already resolved export paths, possibly overridden.
    :return: Explicit database registry with unique names and paths.
    """
    filenames = {
        "hyperliquid-vaults.duckdb": "HYPERLIQUID_DB_PATH",
        "hyperliquid-vaults-hf.duckdb": "HYPERLIQUID_HF_DB_PATH",
        "grvt-vaults.duckdb": "GRVT_DB_PATH",
        "lighter-pools.duckdb": "LIGHTER_DB_PATH",
        "hibachi-vaults.duckdb": "HIBACHI_DB_PATH",
        "apex-vaults.duckdb": "APEX_DB_PATH",
        "derive-v3-mainnet-vaults.duckdb": "DERIVE_V3_DB_PATH",
        "vault-historical-context.duckdb": "HISTORICAL_CONTEXT_DB_PATH",
        "rpc-tracking.duckdb": "RPC_TRACKING_DB_PATH",
    }
    registry = [DuckDBBackup(Path(filename).stem, Path(os.environ.get(variable, str(resolve_rpc_tracking_database_path() if filename == "rpc-tracking.duckdb" else base_path / filename))).expanduser()) for filename, variable in filenames.items()]
    registry.append(DuckDBBackup("vault-post-database", resolve_feed_database_path()))
    currency_path = Path(os.environ.get("CURRENCY_API_DB_PATH") or os.environ.get("CURRENCY_API_DATABASE_PATH") or str(base_path / "exchange-rates.duckdb")).expanduser().resolve()
    canonical = {resolve_core3_database_path().resolve(): "core3", resolve_xerberus_database_path().resolve(): "xerberus", currency_path: "exchange-rates"}
    for path in existing_paths:
        if path.suffix == ".duckdb" and path.resolve() not in {entry.path.resolve() for entry in registry}:
            registry.append(DuckDBBackup(canonical.get(path.resolve(), path.stem), path))
    for extra in json.loads(os.environ.get("DUCKDB_EXTRA_BACKUPS", "[]")):
        registry.append(DuckDBBackup(extra["name"], Path(extra["path"]).expanduser()))
    if len({entry.path.resolve() for entry in registry}) != len(registry):
        msg = "Duplicate database paths in backup registry"
        raise ValueError(msg)
    names = [entry.name for entry in registry]
    if len(names) != len(set(names)):
        msg = "Conflicting logical DuckDB backup identities"
        raise ValueError(msg)
    known = {entry.path.resolve() for entry in registry}
    for path in base_path.glob("*.duckdb"):
        if path.resolve() not in known:
            logger.warning("Unregistered DuckDB is not backed up: %s", path)
    return registry


def _fetch_state(client: Any, bucket: str, key: str, *, recover_from_objects: bool = True) -> tuple[dict, str | None, datetime.datetime | None]:
    """Read the authoritative remote scheduler receipt.

    Missing state is reconstructed from immutable attempt and snapshot objects
    so deleting local or mutable receipt files cannot reset the upload gate.

    :param client: R2 S3 client.
    :param bucket: Explicit private bucket.
    :param key: Scheduler state key.
    :param recover_from_objects: Recover database gate anchors from immutable objects.
    :return: State, conditional-write ETag and latest server UTC clock.
    """
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"NoSuchKey", "404", "NotFound"}:
            raise
        if not recover_from_objects:
            return {}, None, None
        prefix = key.rsplit("/", 1)[0] + "/"
        latest = None
        state = {}
        latest_receipt = None
        latest_receipt_time = None
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                modified = item["LastModified"].replace(tzinfo=None)
                if latest is None or modified > latest:
                    latest = modified
                if item["Key"].endswith("-result.json") and (latest_receipt_time is None or modified > latest_receipt_time):
                    latest_receipt = item["Key"]
                    latest_receipt_time = modified
        if latest_receipt:
            response = client.get_object(Bucket=bucket, Key=latest_receipt)
            try:
                state = json.loads(response["Body"].read())
            finally:
                response["Body"].close()
        return state, None, latest
    else:
        modified = response["LastModified"].replace(tzinfo=None)
        body = response["Body"]
        try:
            return json.loads(body.read()), response["ETag"], modified
        finally:
            body.close()


def fetch_legacy_backup_anchor(client: Any, bucket: str, filename: str, now: datetime.datetime) -> datetime.datetime | None:
    """Bootstrap the new cadence from existing flat and daily legacy transfers.

    The first scheduled backup must also respect uploads/copies made by the old
    exporter during the preceding 48 hours. Only remote HEAD metadata is read.

    :param client: Private R2 client.
    :param bucket: Private bucket.
    :param filename: Existing compatibility object basename.
    :param now: Naive UTC scheduler clock.
    :return: Latest legacy upload/copy timestamp, or ``None`` when absent.
    """
    flat = os.environ.get("UPLOAD_PREFIX", "") + filename
    keys = [flat, *(f"daily/{(now - datetime.timedelta(days=days)).date().isoformat()}/{flat}" for days in range(3))]
    anchors = []
    for key in keys:
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"NoSuchKey", "404", "NotFound"}:
                raise
        else:
            anchors.append(head["LastModified"].replace(tzinfo=None))
    return max(anchors) if anchors else None


def _claim_attempt(client: Any, bucket: str, prefix: str, previous: dict, etag: str | None, *, now: datetime.datetime) -> dict | None:
    """Claim an upload window with an atomic remote compare-and-swap.

    Competing exporters cannot both snapshot or transfer one logical database.
    Failed attempts consume the same 48-hour window as successful attempts.

    :param client: R2 S3 client.
    :param bucket: Private bucket.
    :param prefix: Stable logical database prefix.
    :param previous: Prior scheduler state.
    :param etag: Prior ETag or ``None`` for first creation.
    :param now: Naive UTC attempt time.
    :return: Claim state, or ``None`` when another exporter won the window.
    """
    state = {**previous, "attempt_id": uuid.uuid4().hex, "attempted_at": now.isoformat(), "status": "claimed"}
    conditional = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
    try:
        response = client.put_object(Bucket=bucket, Key=prefix + "state.json", Body=json.dumps(state).encode(), ContentType="application/json", **conditional)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}:
            raise
        return None
    state["_etag"] = response["ETag"]
    client.put_object(Bucket=bucket, Key=prefix + "attempts/" + state["attempt_id"] + ".json", Body=json.dumps({k: v for k, v in state.items() if k != "_etag"}).encode(), ContentType="application/json", IfNoneMatch="*")
    return state


def backup_databases(client: Any, bucket: str, registry: list[DuckDBBackup], now: datetime.datetime | None = None, maintenance_id: str | None = None) -> dict[str, str]:  # noqa: PLR0914
    """Back up due, exclusively opened databases once per two days at most.

    The gate precedes hashing, snapshotting, upload and compatibility copy.
    Snapshot keys are immutable. Unchanged due databases update the check receipt
    without transferring bytes. Errors are observable and throttled too; other
    registered databases are attempted independently. No local file is deleted.

    :param client: Authenticated R2 client with conditional write support.
    :param bucket: Private data bucket only.
    :param registry: Explicit logical database registry.
    :param now: Optional injected naive UTC time for focused tests.
    :param maintenance_id: Named reservation owner; routine exporters pass ``None``.
    :return: Per-database ``missing``, ``deferred``, ``unchanged``, ``uploaded`` or ``failed``.
    """
    backup_key_root()
    now = now or native_datetime_utc_now()
    if now.tzinfo is not None:
        msg = "Expected naive UTC scheduler time"
        raise ValueError(msg)
    results = {}
    for entry in tqdm(registry, desc="Checking DuckDB backup windows"):  # noqa: PLR1702
        try:
            if not entry.path.exists():
                results[entry.name] = "missing"
                continue
            if "/" in entry.name or not entry.name:
                msg = "Invalid database logical name"
                raise ValueError(msg)
            reservation, _, _ = _fetch_state(client, bucket, backup_key_root() + "maintenance.json", recover_from_objects=False)
            active = reservation.get("status") == "reserved" and datetime.datetime.fromisoformat(reservation["expires_at"]) > now
            if active and entry.name in reservation["names"]:
                if reservation["reservation_id"] != maintenance_id or now < datetime.datetime.fromisoformat(reservation["ready_after"]):
                    results[entry.name] = "deferred"
                    logger.info("DuckDB %s deferred for shared maintenance window %s", entry.name, reservation["reservation_id"])
                    continue
            prefix = backup_key_root() + f"{entry.name}/"
            state, etag, remote_time = _fetch_state(client, bucket, prefix + "state.json")
            if etag is None and remote_time is None:
                remote_time = fetch_legacy_backup_anchor(client, bucket, entry.path.name, now)
            attempted = datetime.datetime.fromisoformat(state["attempted_at"]) if state.get("attempted_at") else None
            anchors = [value for value in (attempted, remote_time) if value is not None]
            if anchors and now - max(anchors) < MINIMUM_BACKUP_INTERVAL:
                results[entry.name] = "deferred"
                logger.info("DuckDB backup deferred until %s: %s", max(anchors) + MINIMUM_BACKUP_INTERVAL, entry.name)
                continue
            claim = _claim_attempt(client, bucket, prefix, state, etag, now=now)
            if claim is None:
                results[entry.name] = "deferred"
                continue
            claim_etag = claim.pop("_etag")
            try:
                with tempfile.TemporaryDirectory(prefix="duckdb-backup-") as temporary_dir:
                    snapshot = Path(temporary_dir) / entry.path.name
                    # Opening read/write excludes another process owning a writable
                    # database. A read-only connection or shutil of a live WAL is unsafe.
                    connection = duckdb.connect(str(entry.path))
                    try:
                        with observe_database_operation(f"Checkpoint R2 snapshot: {entry.path}"):
                            connection.execute("CHECKPOINT")
                        copy_database_file(entry.path, snapshot)
                    finally:
                        connection.close()
                    digest = file_sha256(snapshot)
                    if digest == state.get("sha256") and state.get("snapshot_key"):
                        if state.get("compatibility_copy_pending"):
                            key = state["snapshot_key"]
                            head = client.head_object(Bucket=bucket, Key=key)
                            if head.get("Metadata", {}).get("sha256") != digest:
                                msg = "Pending immutable snapshot checksum differs"
                                raise ValueError(msg)
                            client.copy(Bucket=bucket, Key=os.environ.get("UPLOAD_PREFIX", "") + entry.path.name, CopySource={"Bucket": bucket, "Key": key}, ExtraArgs={"CopySourceIfMatch": head["ETag"]})
                            claim.update(status="uploaded", compatibility_copy_pending=False)
                        else:
                            claim["status"] = "unchanged"
                    else:
                        key = prefix + claim["attempt_id"] + ".duckdb"
                        with tqdm(total=snapshot.stat().st_size, unit="B", unit_scale=True, desc=f"Backing up {entry.name}") as bar:
                            client.upload_file(str(snapshot), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}}, Callback=bar.update)
                        head = client.head_object(Bucket=bucket, Key=key)
                        if head["ContentLength"] != snapshot.stat().st_size or head.get("Metadata", {}).get("sha256") != digest:
                            msg = "R2 snapshot verification failed"
                            raise ValueError(msg)
                        # Persist upload progress before the compatibility copy so
                        # a failed copy never causes a redundant snapshot upload.
                        claim.update(status="snapshot_uploaded", snapshot_key=key, sha256=digest, size=snapshot.stat().st_size, compatibility_copy_pending=True)
                        response = client.put_object(Bucket=bucket, Key=prefix + "state.json", Body=json.dumps(claim).encode(), ContentType="application/json", IfMatch=claim_etag)
                        claim_etag = response["ETag"]
                        client.put_object(Bucket=bucket, Key=prefix + "attempts/" + claim["attempt_id"] + "-uploaded-result.json", Body=json.dumps(claim).encode(), ContentType="application/json", IfNoneMatch="*")
                        client.copy(Bucket=bucket, Key=os.environ.get("UPLOAD_PREFIX", "") + entry.path.name, CopySource={"Bucket": bucket, "Key": key}, ExtraArgs={"CopySourceIfMatch": head["ETag"]})
                        claim.update(status="uploaded", compatibility_copy_pending=False)
                    results[entry.name] = claim["status"]
            except (duckdb.Error, OSError, ClientError, BotoCoreError, S3UploadFailedError, ValueError, KeyError, TypeError) as exc:
                claim.update(status="failed", error_type=type(exc).__name__)
                results[entry.name] = "failed"
                logger.warning("DuckDB backup failed for %s; next attempt after 48 hours: %s", entry.name, exc)
            # Persist completion only if this attempt still owns the state. Even if
            # this fails, the immutable claim and server date keep the window closed.
            client.put_object(Bucket=bucket, Key=prefix + "state.json", Body=json.dumps(claim).encode(), ContentType="application/json", IfMatch=claim_etag)
            client.put_object(Bucket=bucket, Key=prefix + "attempts/" + claim["attempt_id"] + "-result.json", Body=json.dumps(claim).encode(), ContentType="application/json", IfNoneMatch="*")
        except (duckdb.Error, OSError, ClientError, BotoCoreError, S3UploadFailedError, ValueError, KeyError, TypeError) as exc:
            results[entry.name] = "failed"
            logger.warning("DuckDB backup/receipt failed for %s: %s", entry.name, exc)
    if any(status == "failed" for status in results.values()):
        raise DuckDBBackupError(results)
    return results


def reserve_backup_window(client: Any, bucket: str, reservation_id: str, names: list[str], *, action: str = "reserve", now: datetime.datetime | None = None) -> dict:
    """Reserve a shared daily/HF maintenance window without scheduler starvation.

    A bucket-wide conditional receipt has one active named reservation. Routine
    exporters defer its databases while it is active; the named maintenance
    exporter still observes every database's original 48-hour gate. Heartbeats
    renew a 72-hour lease; cancel releases it without resetting backup clocks.

    :param client: R2 client.
    :param bucket: Private bucket.
    :param reservation_id: Operator-supplied maintenance name.
    :param names: Logical database identities reserved together.
    :param action: ``reserve``, ``heartbeat`` or ``cancel``.
    :param now: Optional naive UTC test clock.
    :return: Reservation receipt with the earliest common backup time.
    """
    now = now or native_datetime_utc_now()
    key = backup_key_root() + "maintenance.json"
    previous, etag, _ = _fetch_state(client, bucket, key, recover_from_objects=False)
    active = previous.get("status") == "reserved" and datetime.datetime.fromisoformat(previous["expires_at"]) > now
    if active and previous.get("reservation_id") != reservation_id:
        msg = "Another named backup maintenance window is active"
        raise RuntimeError(msg)
    if action not in {"reserve", "heartbeat", "cancel"}:
        msg = "Invalid reservation action"
        raise ValueError(msg)
    if action == "heartbeat" and not active:
        msg = "Cannot renew an expired or missing reservation"
        raise ValueError(msg)
    due = now
    for name in names:
        state, _, remote = _fetch_state(client, bucket, backup_key_root() + f"{name}/state.json")
        attempted = datetime.datetime.fromisoformat(state["attempted_at"]) if state.get("attempted_at") else None
        anchors = [value for value in (attempted, remote) if value is not None]
        if anchors:
            due = max(due, max(anchors) + MINIMUM_BACKUP_INTERVAL)
    deadline = datetime.datetime.fromisoformat(previous["lease_deadline"]) if active and previous.get("lease_deadline") else now + datetime.timedelta(days=7)
    receipt = {"reservation_id": reservation_id, "names": names, "status": "cancelled" if action == "cancel" else "reserved", "ready_after": due.isoformat(), "expires_at": min(now + datetime.timedelta(hours=72), deadline).isoformat(), "lease_deadline": deadline.isoformat()}

    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(receipt).encode(), ContentType="application/json", **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"}))
    return receipt
