"""Durable two-day gates and isolated real R2 snapshot round trips."""

import datetime
import io
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from botocore.exceptions import ClientError

from eth_defi.vault import duckdb_backup
from eth_defi.vault.duckdb_backup import DuckDBBackup, backup_databases, create_private_backup_client, database_registry, reserve_backup_window


class FakeR2:
    """Small conditional object store used to exercise scheduler failure states."""

    def __init__(self) -> None:
        self.objects = {}
        self.clock = datetime.datetime(2026, 10, 6)
        self.transfers = []

    def get_object(self, Bucket: str, Key: str) -> Any:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data, etag, modified, metadata = self.objects[Key]
        return {"Body": io.BytesIO(data), "ETag": etag, "LastModified": modified, "Metadata": metadata}

    def put_object(self, Bucket: str, Key: str, Body: bytes, IfMatch: str | None = None, IfNoneMatch: str | None = None, **kwargs: object) -> Any:
        existing = self.objects.get(Key)
        if (IfNoneMatch == "*" and existing is not None) or (IfMatch and (existing is None or existing[1] != IfMatch)):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        etag = uuid.uuid4().hex
        self.objects[Key] = (Body, etag, self.clock, {})
        return {"ETag": etag}

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict, Callback: Callable[[int], None]) -> None:
        data = Path(filename).read_bytes()
        self.transfers.append(key)
        self.objects[key] = (data, uuid.uuid4().hex, self.clock, ExtraArgs["Metadata"])
        Callback(len(data))

    def copy_object(self, Bucket: str, Key: str, CopySource: dict) -> None:
        self.transfers.append(Key)
        self.objects[Key] = self.objects[CopySource["Key"]]

    def copy(self, Bucket: str, Key: str, CopySource: dict, ExtraArgs: dict) -> None:
        """Emulate the managed SDK copy entry point, preserving failure injection."""
        return self.copy_object(Bucket=Bucket, Key=Key, CopySource=CopySource)

    def head_object(self, Bucket: str, Key: str) -> Any:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NotFound"}}, "HeadObject")
        data, etag, modified, metadata = self.objects[Key]
        return {"ContentLength": len(data), "Metadata": metadata, "ETag": etag, "LastModified": modified}

    def get_paginator(self, name: str) -> Any:
        return self

    def paginate(self, Bucket: str, Prefix: str) -> Any:
        return [{"Contents": [{"Key": key, "LastModified": value[2]} for key, value in self.objects.items() if key.startswith(Prefix)]}]


def make_database(path: Path) -> None:
    """Create a closed real database that can be restored from snapshot bytes."""
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE TABLE values_table AS SELECT 42 AS value")
    finally:
        connection.close()


def test_gate_precedes_hash_snapshot_upload_and_copy(tmp_path: Path) -> None:
    """A changed DB during a skipped cycle causes zero snapshot or transfer work.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "db.duckdb"
    make_database(path)
    client = FakeR2()
    registry = [DuckDBBackup("test", path)]
    assert backup_databases(client, "private", registry, now=client.clock) == {"test": "uploaded"}
    assert len(client.transfers) == 2
    client.clock += datetime.timedelta(days=1)
    with patch("eth_defi.vault.duckdb_backup.file_sha256", side_effect=AssertionError("must not hash")), patch("eth_defi.vault.duckdb_backup.duckdb.connect", side_effect=AssertionError("must not open")):
        assert backup_databases(client, "private", registry, now=client.clock) == {"test": "deferred"}
    assert len(client.transfers) == 2
    client.clock += datetime.timedelta(days=1)
    assert backup_databases(client, "private", registry, now=client.clock) == {"test": "unchanged"}
    assert len(client.transfers) == 2
    assert backup_databases(client, "private", registry, now=client.clock) == {"test": "deferred"}


def test_lost_receipt_and_clock_rollback_do_not_reset_window(tmp_path: Path) -> None:
    """Immutable remote claims survive receipt loss; clock rollback defers work.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "db.duckdb"
    make_database(path)
    client = FakeR2()
    registry = [DuckDBBackup("test", path)]
    backup_databases(client, "private", registry, now=client.clock)
    del client.objects["duckdb-backups/test/state.json"]
    with patch("eth_defi.vault.duckdb_backup.file_sha256", side_effect=AssertionError("must not hash")):
        assert backup_databases(client, "private", registry, now=client.clock - datetime.timedelta(days=1)) == {"test": "deferred"}
        assert backup_databases(client, "private", registry, now=client.clock + datetime.timedelta(days=1)) == {"test": "deferred"}


def test_failed_snapshot_is_throttled(tmp_path: Path) -> None:
    """Failed attempts cannot retry a transfer on every scanner cycle.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "not-a-db.duckdb"
    path.write_bytes(b"not a database")
    client = FakeR2()
    registry = [DuckDBBackup("broken", path)]
    with pytest.raises(RuntimeError, match="broken"):
        backup_databases(client, "private", registry, now=client.clock)
    assert backup_databases(client, "private", registry, now=client.clock + datetime.timedelta(hours=1)) == {"broken": "deferred"}


@pytest.mark.skipif(os.environ.get("RUN_R2_BACKUP_INTEGRATION") != "true", reason="Set RUN_R2_BACKUP_INTEGRATION=true with private R2 credentials")
def test_real_r2_backup_round_trip(tmp_path: Path) -> None:
    """Perform authenticated conditional writes, one transfer and a restore.

    All keys, including compatibility copies, are isolated below a random
    integration prefix and cleaned up afterwards. Never overwrite live keys.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    bucket = os.environ["R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"]
    client = create_private_backup_client()
    prefix = "integration/hypercore-1628-" + uuid.uuid4().hex + "/"

    class IsolatedR2:
        """Prefix every request, including server copies, to isolate live testing."""

        def get_paginator(self, name: str) -> Any:
            paginator = client.get_paginator(name)

            class Pages:
                def paginate(self, **kwargs: object) -> Any:
                    kwargs["Prefix"] = prefix + kwargs["Prefix"]
                    return paginator.paginate(**kwargs)

            return Pages()

        def upload_file(self, filename: str, bucket_name: str, key: str, **kwargs: object) -> None:
            return client.upload_file(filename, bucket_name, prefix + key, **kwargs)

        def __getattr__(self, name: str) -> Any:
            def call(**kwargs: object) -> Any:
                if "Key" in kwargs:
                    kwargs["Key"] = prefix + kwargs["Key"]
                if "CopySource" in kwargs:
                    kwargs["CopySource"] = {**kwargs["CopySource"], "Key": prefix + kwargs["CopySource"]["Key"]}
                return getattr(client, name)(**kwargs)

            return call

    path = tmp_path / "source.duckdb"
    make_database(path)
    isolated = IsolatedR2()
    try:
        registry = [DuckDBBackup("test", path)]
        assert backup_databases(isolated, bucket, registry) == {"test": "uploaded"}
        with patch("eth_defi.vault.duckdb_backup.file_sha256", side_effect=AssertionError("second cycle must skip hash")):
            assert backup_databases(isolated, bucket, registry) == {"test": "deferred"}
        response = isolated.get_object(Bucket=bucket, Key="duckdb-backups/test/state.json")
        try:
            state = json.loads(response["Body"].read())
        finally:
            response["Body"].close()
        restored = tmp_path / "restored.duckdb"
        client.download_file(bucket, prefix + state["snapshot_key"], str(restored))
        connection = duckdb.connect(str(restored), read_only=True)
        try:
            assert connection.execute("SELECT value FROM values_table").fetchone()[0] == 42
        finally:
            connection.close()
        # Verify the provider actually rejects stale conditional writes.
        with pytest.raises(ClientError) as exc:
            isolated.put_object(Bucket=bucket, Key="duckdb-backups/test/state.json", Body=b"{}", IfMatch='"stale-etag"')
        assert exc.value.response["Error"]["Code"] in {"PreconditionFailed", "412"}
    finally:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=bucket, Delete={"Objects": keys})


def test_maintenance_reservation_defers_routine_until_shared_gate(tmp_path: Path) -> None:
    """Routine cycles cannot consume one member of a reserved daily/HF pair.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """

    client = FakeR2()
    daily, hf = tmp_path / "daily.duckdb", tmp_path / "hf.duckdb"
    make_database(daily)
    make_database(hf)
    registry = [DuckDBBackup("daily", daily), DuckDBBackup("hf", hf)]
    backup_databases(client, "private", registry[:1], now=client.clock)
    client.clock += datetime.timedelta(days=1)
    backup_databases(client, "private", registry[1:], now=client.clock)
    receipt = reserve_backup_window(client, "private", "repair", names=["daily", "hf"], now=client.clock)
    due = datetime.datetime.fromisoformat(receipt["ready_after"])
    assert due == client.clock + datetime.timedelta(days=2)
    client.clock += datetime.timedelta(days=1)
    assert backup_databases(client, "private", registry, now=client.clock) == {"daily": "deferred", "hf": "deferred"}
    client.clock = due
    assert backup_databases(client, "private", registry, now=client.clock, maintenance_id="repair") == {"daily": "unchanged", "hf": "unchanged"}
    reserve_backup_window(client, "private", "repair", ["daily", "hf"], action="cancel", now=client.clock)
    assert backup_databases(client, "private", registry, now=client.clock) == {"daily": "deferred", "hf": "deferred"}


def test_failed_copy_resumes_without_reupload(tmp_path: Path) -> None:
    """A compatibility-copy failure resumes only that copy after its next gate.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "db.duckdb"
    make_database(path)
    client = FakeR2()
    registry = [DuckDBBackup("test", path)]
    original_copy = client.copy_object

    def fail_copy(**kwargs: object) -> Any:
        raise ClientError({"Error": {"Code": "InternalError"}}, "CopyObject")

    client.copy_object = fail_copy
    with pytest.raises(RuntimeError, match="test"):
        backup_databases(client, "private", registry, now=client.clock)
    assert len(client.transfers) == 1
    client.copy_object = original_copy
    client.clock += datetime.timedelta(days=2)
    assert backup_databases(client, "private", registry, now=client.clock) == {"test": "uploaded"}
    assert len(client.transfers) == 2


def test_registry_path_override_preserves_logical_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A differently named database override cannot reset its logical cadence.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :param monkeypatch: Replace transports or inject a controlled failure.
    :return: ``None`` after validating the expected behaviour.
    """

    path = tmp_path / "renamed.duckdb"
    monkeypatch.setenv("HYPERLIQUID_HF_DB_PATH", str(path))
    registry = database_registry(tmp_path, [])
    assert next(entry for entry in registry if entry.path == path).name == "hyperliquid-vaults-hf"


def test_first_scheduler_cycle_respects_recent_legacy_flat_and_daily_copies(tmp_path: Path) -> None:
    """Enabling the gate cannot upload again within 48h of an old exporter copy.

    Exercise the remote scheduler or restore path with isolated database
    state, checking cadence and failure behaviour.
    :param tmp_path: Isolated file-backed database and evidence directory.
    :return: ``None`` after validating the expected behaviour.
    """
    path = tmp_path / "db.duckdb"
    make_database(path)
    client = FakeR2()
    old = client.clock - datetime.timedelta(days=4)
    recent = client.clock - datetime.timedelta(hours=1)
    client.objects["db.duckdb"] = (b"old", "etag", old, {})
    client.objects["daily/2026-10-06/db.duckdb"] = (b"old", "etag", recent, {})
    with patch("eth_defi.vault.duckdb_backup.file_sha256", side_effect=AssertionError("legacy gate must precede hash")):
        assert backup_databases(client, "private", [DuckDBBackup("test", path)], now=client.clock) == {"test": "deferred"}
    assert client.transfers == []
    client.clock = recent + datetime.timedelta(days=2)
    assert backup_databases(client, "private", [DuckDBBackup("test", path)], now=client.clock) == {"test": "uploaded"}


@pytest.mark.parametrize("data_credentials", [True, False])
def test_private_backup_client_credential_precedence(monkeypatch: pytest.MonkeyPatch, data_credentials: bool) -> None:
    """Private data credentials take precedence, with metadata credentials as fallback.

    Exercise the shared client configuration without printing or using a real
    secret; the isolated authenticated integration test covers provider access.

    :param monkeypatch: Supply fixture settings and replace the client factory.
    :param data_credentials: Whether the preferred environment variables exist.
    :return: ``None`` after checking all three resolved client arguments.
    """
    for suffix in ("ENDPOINT_URL", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.setenv("R2_VAULT_METADATA_" + suffix, "metadata-" + suffix)
        if data_credentials:
            monkeypatch.setenv("R2_DATA_" + suffix, "data-" + suffix)
        else:
            monkeypatch.delenv("R2_DATA_" + suffix, raising=False)
    monkeypatch.setattr(duckdb_backup, "create_r2_client", lambda **kwargs: kwargs)
    origin = "data-" if data_credentials else "metadata-"
    assert create_private_backup_client() == {"endpoint_url": origin + "ENDPOINT_URL", "access_key_id": origin + "ACCESS_KEY_ID", "secret_access_key": origin + "SECRET_ACCESS_KEY"}
