"""Unit tests for Cloudflare R2 upload helpers."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from eth_defi.cloudflare_r2 import calculate_bytes_digest, calculate_file_digest, upload_bytes_to_r2, upload_file_to_r2

try:
    from botocore.exceptions import ClientError
except ModuleNotFoundError:  # pragma: no cover - exercised in CI dependency matrix
    pytestmark = pytest.mark.skip(reason="Cloudflare R2 tests require optional boto3/botocore dependency")

    class ClientError(Exception):
        """Fallback exception for optional botocore-free environments."""


class FakeS3Client:
    """Small in-memory S3 client stub for upload helper tests."""

    def __init__(self) -> None:
        self.head_responses: dict[tuple[str, str], dict] = {}
        self.put_calls: list[dict] = []
        self.upload_calls: list[dict] = []

    def head_object(self, **kwargs) -> dict:
        """Return a stored head response or raise a not-found error."""
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
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
