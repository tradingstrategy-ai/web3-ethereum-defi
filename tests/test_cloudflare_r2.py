"""Unit tests for Cloudflare R2 upload helpers."""

from __future__ import annotations

import gzip
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.cloudflare_r2 import (
    R2AccessDeniedError,
    calculate_bytes_digest,
    calculate_file_digest,
    copy_r2_object_daily_backup,
    fetch_r2_object_head,
    upload_bytes_to_r2,
    upload_file_to_r2,
)
from eth_defi.compat import native_datetime_utc_now

try:
    from botocore.exceptions import ClientError
except ModuleNotFoundError:  # pragma: no cover - exercised in CI dependency matrix
    pytestmark = pytest.mark.skip(reason="Cloudflare R2 tests require optional boto3/botocore dependency")

    class ClientError(Exception):
        """Fallback exception for optional botocore-free environments."""


class FakeS3Client:
    """Small in-memory S3 client stub for upload helper tests."""

    def __init__(
        self,
        endpoint_url: str = "https://db6295230fa08e641c3ce159dcda30a8.r2.cloudflarestorage.com",
        access_key_id: str = "e24301234567dac",
    ) -> None:
        self.head_responses: dict[tuple[str, str], dict] = {}
        self.head_exceptions: dict[tuple[str, str], Exception] = {}
        self.put_calls: list[dict] = []
        self.upload_calls: list[dict] = []
        self.copy_calls: list[dict] = []
        self.meta = SimpleNamespace(endpoint_url=endpoint_url)
        self._request_signer = SimpleNamespace(_credentials=SimpleNamespace(access_key=access_key_id))

    def head_object(self, **kwargs) -> dict:
        """Return a stored head response or raise a not-found error."""
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        forced_exception = self.head_exceptions.get((bucket, key))
        if forced_exception is not None:
            raise forced_exception
        try:
            return self.head_responses[bucket, key]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject") from exc

    def put_object(self, **kwargs) -> None:
        """Record a put-object call and update the stored head state."""
        self.put_calls.append(kwargs)
        self.head_responses[kwargs["Bucket"], kwargs["Key"]] = {
            "ContentLength": len(kwargs["Body"]),
            "ContentType": kwargs.get("ContentType"),
            "ContentEncoding": kwargs.get("ContentEncoding"),
            "Metadata": kwargs.get("Metadata", {}),
            "ETag": f'"{_md5_hex(kwargs["Body"])}"',
        }

    def copy_object(self, **kwargs) -> None:
        """Record a copy-object call and duplicate the head state."""
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        copy_exc = self.head_exceptions.get(("copy", bucket, key))
        if copy_exc is not None:
            raise copy_exc
        source = kwargs["CopySource"]
        src_bucket = source["Bucket"]
        src_key = source["Key"]
        self.copy_calls.append(kwargs)
        if (src_bucket, src_key) in self.head_responses:
            self.head_responses[bucket, key] = dict(self.head_responses[src_bucket, src_key])

    def upload_fileobj(self, fileobj, bucket_name: str, object_name: str, **kwargs) -> None:
        """Record an upload-fileobj call and update the stored head state."""
        payload = fileobj.read()
        callback = kwargs.get("Callback")
        if callback is not None:
            callback(len(payload))

        extra_args = kwargs.get("ExtraArgs") or {}
        self.upload_calls.append(
            {
                "Bucket": bucket_name,
                "Key": object_name,
                "Body": payload,
                "ExtraArgs": extra_args,
            }
        )
        self.head_responses[bucket_name, object_name] = {
            "ContentLength": len(payload),
            "ContentType": extra_args.get("ContentType"),
            "Metadata": extra_args.get("Metadata", {}),
        }


def _md5_hex(payload: bytes) -> str:
    """Calculate an MD5 digest for legacy ETag test fixtures."""
    return hashlib.md5(payload, usedforsecurity=False).hexdigest()


def test_upload_bytes_to_r2_skips_when_remote_checksum_matches() -> None:
    """Checksum metadata should skip unchanged compressed payload uploads."""
    s3_client = FakeS3Client()
    payload = b'{"slug":"lagoon-finance"}'
    compressed_payload = gzip.compress(payload, mtime=0)
    source_digest = calculate_bytes_digest(payload)

    s3_client.head_responses["bucket", "metadata.json"] = {
        "ContentLength": len(compressed_payload),
        "ContentType": "application/json",
        "ContentEncoding": "gzip",
        "Metadata": source_digest.as_metadata(),
    }

    uploaded = upload_bytes_to_r2(
        s3_client=s3_client,
        payload=compressed_payload,
        bucket_name="bucket",
        object_name="metadata.json",
        content_type="application/json",
        content_encoding="gzip",
        skip_if_current=True,
        source_digest=source_digest,
    )

    assert uploaded is False
    assert s3_client.put_calls == []


def test_upload_bytes_to_r2_skips_with_matching_legacy_etag() -> None:
    """Legacy single-part uploads should still skip via matching ETag."""
    s3_client = FakeS3Client()
    payload = b'{"slug":"untangle-finance"}'
    compressed_payload = gzip.compress(payload, mtime=0)

    s3_client.head_responses["bucket", "metadata.json"] = {
        "ContentLength": len(compressed_payload),
        "ContentType": "application/json",
        "ContentEncoding": "gzip",
        "Metadata": {},
        "ETag": f'"{_md5_hex(compressed_payload)}"',
    }

    uploaded = upload_bytes_to_r2(
        s3_client=s3_client,
        payload=compressed_payload,
        bucket_name="bucket",
        object_name="metadata.json",
        content_type="application/json",
        content_encoding="gzip",
        skip_if_current=True,
        source_digest=calculate_bytes_digest(payload),
    )

    assert uploaded is False
    assert s3_client.put_calls == []


def test_upload_bytes_to_r2_uploads_and_sets_checksum_metadata() -> None:
    """Fresh uploads should persist checksum metadata for later HEAD checks."""
    s3_client = FakeS3Client()
    payload = b"vault metadata"

    uploaded = upload_bytes_to_r2(
        s3_client=s3_client,
        payload=payload,
        bucket_name="bucket",
        object_name="metadata.json",
        content_type="application/json",
        skip_if_current=True,
    )

    assert uploaded is True
    assert len(s3_client.put_calls) == 1
    assert s3_client.put_calls[0]["Metadata"] == calculate_bytes_digest(payload).as_metadata()


def test_upload_file_to_r2_skips_when_remote_checksum_matches(tmp_path: Path) -> None:
    """File uploads should skip when stored checksum metadata matches."""
    s3_client = FakeS3Client()
    file_path = tmp_path / "vault-prices-1h.parquet"
    file_path.write_bytes(b"parquet-data")
    source_digest = calculate_file_digest(file_path)

    s3_client.head_responses["bucket", file_path.name] = {
        "ContentLength": file_path.stat().st_size,
        "Metadata": source_digest.as_metadata(),
    }

    uploaded = upload_file_to_r2(
        s3_client=s3_client,
        file_path=file_path,
        bucket_name="bucket",
        object_name=file_path.name,
        skip_if_current=True,
    )

    assert uploaded is False
    assert s3_client.upload_calls == []


def test_upload_file_to_r2_uploads_when_remote_checksum_differs(tmp_path: Path) -> None:
    """File uploads should refresh objects with missing or stale checksums."""
    s3_client = FakeS3Client()
    file_path = tmp_path / "vault-reader-state-1h.pickle"
    file_path.write_bytes(b"reader-state")

    s3_client.head_responses["bucket", file_path.name] = {
        "ContentLength": file_path.stat().st_size,
        "Metadata": {},
    }

    uploaded = upload_file_to_r2(
        s3_client=s3_client,
        file_path=file_path,
        bucket_name="bucket",
        object_name=file_path.name,
        skip_if_current=True,
    )

    assert uploaded is True
    assert len(s3_client.upload_calls) == 1
    assert s3_client.upload_calls[0]["ExtraArgs"]["Metadata"] == calculate_file_digest(file_path).as_metadata()


def test_fetch_r2_object_head_raises_enriched_access_denied_error() -> None:
    """403 responses should include Cloudflare-specific credential hints."""
    s3_client = FakeS3Client()
    s3_client.head_exceptions["bucket", "metadata.json"] = ClientError(
        {
            "Error": {"Code": "403", "Message": "Forbidden"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "HeadObject",
    )

    with pytest.raises(R2AccessDeniedError) as exc_info:
        fetch_r2_object_head(s3_client, "bucket", "metadata.json")

    message = str(exc_info.value)
    assert "HeadObject" in message
    assert "wrong R2 access key ID" in message
    assert "wrong R2 secret access key" in message
    assert "Cloudflare account ID from endpoint" in message
    assert "e243...7dac" in message
    assert isinstance(exc_info.value.__cause__, ClientError)


def test_upload_file_to_r2_continues_after_head_access_denied(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Upload should continue when the skip-if-current HEAD check is forbidden."""
    s3_client = FakeS3Client()
    file_path = tmp_path / "top_vaults_by_chain.json"
    file_path.write_bytes(b'{"vaults":[]}')
    s3_client.head_exceptions["bucket", file_path.name] = ClientError(
        {
            "Error": {"Code": "403", "Message": "Forbidden"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "HeadObject",
    )

    with caplog.at_level(logging.WARNING):
        uploaded = upload_file_to_r2(
            s3_client=s3_client,
            file_path=file_path,
            bucket_name="bucket",
            object_name=file_path.name,
            skip_if_current=True,
        )

    assert uploaded is True
    assert len(s3_client.upload_calls) == 1
    assert "Proceeding with upload attempt anyway" in caplog.text


def test_copy_r2_object_daily_backup_creates_copy() -> None:
    """Server-side copy should be issued when no backup exists for today."""
    s3_client = FakeS3Client()
    # Simulate a live file already in the bucket.
    s3_client.head_responses["bucket", "vault-prices-1h.parquet"] = {
        "ContentLength": 100,
        "Metadata": {},
    }

    copied = copy_r2_object_daily_backup(
        s3_client=s3_client,
        bucket_name="bucket",
        source_key="vault-prices-1h.parquet",
    )

    today = native_datetime_utc_now().strftime("%Y-%m-%d")
    expected_key = f"daily/{today}/vault-prices-1h.parquet"

    assert copied is True
    assert len(s3_client.copy_calls) == 1
    assert s3_client.copy_calls[0]["Key"] == expected_key
    assert s3_client.copy_calls[0]["CopySource"] == {"Bucket": "bucket", "Key": "vault-prices-1h.parquet"}


def test_copy_r2_object_daily_backup_skips_when_exists() -> None:
    """Backup should be skipped when today's copy already exists."""
    s3_client = FakeS3Client()
    today = native_datetime_utc_now().strftime("%Y-%m-%d")
    backup_key = f"daily/{today}/vault-prices-1h.parquet"

    # Pre-populate — backup already exists.
    s3_client.head_responses["bucket", backup_key] = {
        "ContentLength": 100,
        "Metadata": {},
    }

    copied = copy_r2_object_daily_backup(
        s3_client=s3_client,
        bucket_name="bucket",
        source_key="vault-prices-1h.parquet",
    )

    assert copied is False
    assert s3_client.copy_calls == []


def test_copy_r2_object_daily_backup_preserves_upload_prefix() -> None:
    """Full source key including upload prefix should appear in backup key."""
    s3_client = FakeS3Client()
    source_key = "test-vault-prices-1h.parquet"
    s3_client.head_responses["bucket", source_key] = {
        "ContentLength": 50,
        "Metadata": {},
    }

    copied = copy_r2_object_daily_backup(
        s3_client=s3_client,
        bucket_name="bucket",
        source_key=source_key,
    )

    today = native_datetime_utc_now().strftime("%Y-%m-%d")
    expected_key = f"daily/{today}/test-vault-prices-1h.parquet"

    assert copied is True
    assert len(s3_client.copy_calls) == 1
    assert s3_client.copy_calls[0]["Key"] == expected_key


def test_copy_r2_object_daily_backup_returns_false_on_error(caplog: pytest.LogCaptureFixture) -> None:
    """Copy failure should log a warning and return False, never raise."""
    s3_client = FakeS3Client()
    s3_client.head_responses["bucket", "data.parquet"] = {
        "ContentLength": 10,
        "Metadata": {},
    }

    today = native_datetime_utc_now().strftime("%Y-%m-%d")
    backup_key = f"daily/{today}/data.parquet"

    # Force copy_object to raise.
    s3_client.head_exceptions[("copy", "bucket", backup_key)] = ClientError(
        {
            "Error": {"Code": "AccessDenied", "Message": "Access Denied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "CopyObject",
    )

    with caplog.at_level(logging.WARNING):
        copied = copy_r2_object_daily_backup(
            s3_client=s3_client,
            bucket_name="bucket",
            source_key="data.parquet",
        )

    assert copied is False
    assert "Daily backup copy failed" in caplog.text
