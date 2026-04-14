"""Cloudflare R2 upload helpers.

These helpers add cheap ``head_object()``-based change detection for
uploads. By storing source checksums in S3 object metadata, callers can
skip unchanged uploads without downloading the remote object body.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


#: Custom S3 metadata key for the source payload SHA-256 digest.
R2_SOURCE_SHA256_METADATA_KEY = "source_sha256"

#: Custom S3 metadata key for the source payload byte length.
R2_SOURCE_SIZE_METADATA_KEY = "source_size"


@dataclass(slots=True)
class R2SourceDigest:
    """Checksum metadata for a source payload.

    The digest always describes the original source payload before any
    transport encoding such as gzip is applied. This makes checksum
    comparisons stable even if the upload body is encoded differently.

    :param sha256:
        Hex-encoded SHA-256 digest of the source payload.

    :param size:
        Source payload length in bytes.
    """

    #: Hex-encoded SHA-256 digest of the source payload.
    sha256: str

    #: Source payload length in bytes.
    size: int

    def as_metadata(self) -> dict[str, str]:
        """Convert the digest to S3 metadata fields.

        The return value is suitable for ``put_object()`` or
        ``upload_fileobj()`` ``Metadata`` arguments.

        :return:
            Custom S3 metadata mapping.
        """
        return {
            R2_SOURCE_SHA256_METADATA_KEY: self.sha256,
            R2_SOURCE_SIZE_METADATA_KEY: str(self.size),
        }


class R2OperationError(RuntimeError):
    """Raised when an R2 operation fails with enriched diagnostics."""


class R2AccessDeniedError(R2OperationError):
    """Raised when R2 rejects an operation because access is denied."""


def _mask_access_key_id(access_key_id: str | None) -> str:
    """Mask an access key ID for safe logging.

    :param access_key_id:
        Raw access key ID from the boto3 client credentials.

    :return:
        Masked access key ID safe to emit in logs.
    """
    if not access_key_id:
        return "<unknown>"
    if len(access_key_id) <= 8:
        return access_key_id
    return f"{access_key_id[:4]}...{access_key_id[-4:]}"


def _extract_r2_client_context(s3_client: Any) -> tuple[str | None, str | None, str | None]:
    """Extract endpoint, access key and account ID from an R2 client.

    :param s3_client:
        boto3-compatible S3 client.

    :return:
        Tuple of endpoint URL, access key ID and parsed account ID.
    """
    endpoint_url = getattr(getattr(s3_client, "meta", None), "endpoint_url", None)
    credentials = getattr(getattr(s3_client, "_request_signer", None), "_credentials", None)
    access_key_id = getattr(credentials, "access_key", None)

    account_id = None
    if endpoint_url:
        hostname = urlparse(endpoint_url).hostname or ""
        if hostname.endswith(".r2.cloudflarestorage.com"):
            account_id = hostname.removesuffix(".r2.cloudflarestorage.com")

    return endpoint_url, access_key_id, account_id


def _create_r2_operation_error(
    exc: Exception,
    s3_client: Any,
    bucket_name: str,
    object_name: str,
) -> R2OperationError:
    """Create an enriched exception for an R2 client failure.

    The returned exception is intended to be raised ``from`` the
    original botocore error so the full traceback remains available.

    :param exc:
        Original botocore client error.

    :param s3_client:
        boto3-compatible S3 client.

    :param bucket_name:
        Target bucket name.

    :param object_name:
        Target object key.

    :return:
        Enriched exception instance.
    """
    operation_name = getattr(exc, "operation_name", "<unknown>")
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    error_code = str(error.get("Code", "")) or "<unknown>"
    error_message = str(error.get("Message", "")) or "<no message>"
    http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", "<unknown>")
    endpoint_url, access_key_id, account_id = _extract_r2_client_context(s3_client)
    masked_access_key_id = _mask_access_key_id(access_key_id)

    detail_lines = [
        f"R2 {operation_name} failed for bucket={bucket_name!r}, key={object_name!r}, error_code={error_code!r}, http_status={http_status!r}.",
        f"Endpoint URL: {endpoint_url or '<unknown>'}",
        f"Access key ID: {masked_access_key_id}",
    ]
    if account_id:
        detail_lines.append(f"Cloudflare account ID from endpoint: {account_id}")

    if error_code == "InvalidAccessKeyId":
        detail_lines.append("Likely cause: the R2 access key ID is wrong, revoked, or belongs to a different Cloudflare account.")
        return R2AccessDeniedError(" ".join(detail_lines))

    if error_code == "SignatureDoesNotMatch":
        detail_lines.append("Likely cause: the R2 secret access key does not match the access key ID, or the endpoint account ID is wrong.")
        return R2AccessDeniedError(" ".join(detail_lines))

    if error_code in {"403", "AccessDenied", "Forbidden"} or http_status == 403:
        detail_lines.append("Likely causes: wrong R2 access key ID; wrong R2 secret access key; wrong Cloudflare account ID in the endpoint URL; wrong bucket name; or missing read/write permission for this bucket.")
        detail_lines.append(f"Original R2 error message: {error_message}")
        return R2AccessDeniedError(" ".join(detail_lines))

    detail_lines.append(f"Original R2 error message: {error_message}")
    return R2OperationError(" ".join(detail_lines))


def create_r2_client(
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    max_pool_connections: int | None = None,
) -> Any:
    """Create an authenticated Cloudflare R2 S3 client.

    ``boto3`` is imported lazily because the Cloudflare R2 dependency is
    optional for this library.

    :param endpoint_url:
        R2 S3-compatible API endpoint URL.

    :param access_key_id:
        R2 access key ID.

    :param secret_access_key:
        R2 secret access key.

    :param max_pool_connections:
        Optional connection pool size override for concurrent uploads.

    :return:
        Configured boto3 S3 client.
    """
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    client_kwargs: dict[str, Any] = {
        "endpoint_url": endpoint_url,
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
        "region_name": "auto",
    }
    if max_pool_connections is not None:
        client_kwargs["config"] = Config(max_pool_connections=max_pool_connections)

    return boto3.client("s3", **client_kwargs)


def calculate_bytes_digest(payload: bytes) -> R2SourceDigest:
    """Calculate checksum metadata for an in-memory payload.

    :param payload:
        Raw source payload bytes.

    :return:
        SHA-256 digest and source size.
    """
    return R2SourceDigest(
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def calculate_file_digest(file_path: Path) -> R2SourceDigest:
    """Calculate checksum metadata for a file on disk.

    The file is streamed in chunks so large parquet and pickle files do
    not need to be loaded into memory in one go.

    :param file_path:
        Path to the source file.

    :return:
        SHA-256 digest and source size.
    """
    sha256 = hashlib.sha256()

    with file_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha256.update(chunk)

    return R2SourceDigest(
        sha256=sha256.hexdigest(),
        size=file_path.stat().st_size,
    )


def fetch_r2_object_head(
    s3_client: Any,
    bucket_name: str,
    object_name: str,
) -> dict[str, Any] | None:
    """Fetch object metadata using ``head_object()``.

    Missing objects return ``None``. Any other R2 error is re-raised as
    an enriched runtime exception with Cloudflare-specific diagnostics,
    while preserving the original botocore exception as the nested
    cause.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param bucket_name:
        Target R2 bucket name.

    :param object_name:
        Object key inside the bucket.

    :return:
        ``head_object()`` response, or ``None`` if the object does not
        exist.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        return s3_client.head_object(Bucket=bucket_name, Key=object_name)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise _create_r2_operation_error(exc, s3_client, bucket_name, object_name) from exc


def _calculate_md5_hex(payload: bytes) -> str:
    """Calculate MD5 for S3 ETag comparisons.

    Some Python environments expose the ``usedforsecurity`` argument and
    some do not. This helper keeps the call portable.

    :param payload:
        Bytes to hash.

    :return:
        Hex-encoded MD5 digest.
    """
    try:
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()
    except TypeError:
        return hashlib.md5(payload).hexdigest()  # noqa: S324


def _is_remote_object_current(  # noqa: PLR0917
    remote_head: dict[str, Any],
    source_digest: R2SourceDigest,
    expected_length: int,
    content_type: str | None = None,
    content_encoding: str | None = None,
    payload_md5: str | None = None,
) -> bool:
    """Check whether a remote object already matches a local source.

    First we compare the checksum metadata written by this helper. For
    older uploads without checksum metadata, we fall back to an ETag
    comparison for single-part uploads when an MD5 digest is available.

    :param remote_head:
        ``head_object()`` response for the remote object.

    :param source_digest:
        Digest of the original source payload.

    :param expected_length:
        Expected remote object length in bytes.

    :param content_type:
        Expected MIME type, if relevant for this upload.

    :param content_encoding:
        Expected content encoding, if relevant for this upload.

    :param payload_md5:
        Optional MD5 digest of the exact uploaded body for ETag fallback.

    :return:
        ``True`` if the remote object already matches the local source.
    """
    if remote_head.get("ContentLength") != expected_length:
        return False

    if content_type is not None and remote_head.get("ContentType") != content_type:
        return False

    if content_encoding is not None and remote_head.get("ContentEncoding") != content_encoding:
        return False

    metadata = {key.lower(): value for key, value in (remote_head.get("Metadata") or {}).items()}

    if metadata.get(R2_SOURCE_SHA256_METADATA_KEY) == source_digest.sha256 and metadata.get(R2_SOURCE_SIZE_METADATA_KEY) == str(source_digest.size):
        return True

    etag = str(remote_head.get("ETag", "")).strip('"')
    if payload_md5 and etag and "-" not in etag and etag == payload_md5:
        return True

    return False


def upload_bytes_to_r2(
    s3_client: Any,
    payload: bytes,
    bucket_name: str,
    object_name: str,
    *,
    content_type: str | None = None,
    content_encoding: str | None = None,
    skip_if_current: bool = False,
    source_digest: R2SourceDigest | None = None,
) -> bool:
    """Upload an in-memory payload to R2.

    When ``skip_if_current`` is enabled, the helper performs a cheap
    ``head_object()`` request and compares remote metadata against the
    local checksum before uploading.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param payload:
        Exact bytes that will be sent to R2.

    :param bucket_name:
        Target R2 bucket name.

    :param object_name:
        Destination object key.

    :param content_type:
        Optional MIME type for the upload.

    :param content_encoding:
        Optional content encoding for the upload.

    :param skip_if_current:
        Skip the upload if the existing object already matches the local
        source payload.

    :param source_digest:
        Optional digest of the original source payload. If omitted, the
        upload body itself is used as the source payload.

    :return:
        ``True`` if the object was uploaded, ``False`` if it was skipped
        as unchanged.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    source_digest = source_digest or calculate_bytes_digest(payload)

    if skip_if_current:
        try:
            remote_head = fetch_r2_object_head(s3_client, bucket_name, object_name)
        except R2AccessDeniedError as exc:
            logger.warning(
                "R2 HeadObject pre-flight failed for s3://%s/%s while checking whether upload can be skipped. Proceeding with upload attempt anyway. %s",
                bucket_name,
                object_name,
                exc,
            )
        else:
            if remote_head and _is_remote_object_current(
                remote_head=remote_head,
                source_digest=source_digest,
                expected_length=len(payload),
                content_type=content_type,
                content_encoding=content_encoding,
                payload_md5=_calculate_md5_hex(payload),
            ):
                return False

    put_kwargs: dict[str, Any] = {
        "Bucket": bucket_name,
        "Key": object_name,
        "Body": payload,
        "Metadata": source_digest.as_metadata(),
    }
    if content_type is not None:
        put_kwargs["ContentType"] = content_type
    if content_encoding is not None:
        put_kwargs["ContentEncoding"] = content_encoding

    try:
        s3_client.put_object(**put_kwargs)
    except ClientError as exc:
        raise _create_r2_operation_error(exc, s3_client, bucket_name, object_name) from exc

    return True


def upload_file_to_r2(
    s3_client: Any,
    file_path: Path,
    bucket_name: str,
    object_name: str,
    *,
    skip_if_current: bool = False,
    content_type: str | None = None,
    callback: Callable[[int], None] | None = None,
) -> bool:
    """Upload a file from disk to R2.

    The helper stores checksum metadata for the source file so later runs
    can skip unchanged uploads using a ``head_object()`` request alone.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param file_path:
        Source file path on disk.

    :param bucket_name:
        Target R2 bucket name.

    :param object_name:
        Destination object key.

    :param skip_if_current:
        Skip the upload if the remote object already matches the local
        file checksum.

    :param content_type:
        Optional MIME type for the upload.

    :param callback:
        Optional boto3 progress callback.

    :return:
        ``True`` if the file was uploaded, ``False`` if it was skipped as
        unchanged.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    source_digest = calculate_file_digest(file_path)

    if skip_if_current:
        try:
            remote_head = fetch_r2_object_head(s3_client, bucket_name, object_name)
        except R2AccessDeniedError as exc:
            logger.warning(
                "R2 HeadObject pre-flight failed for s3://%s/%s while checking whether upload can be skipped. Proceeding with upload attempt anyway. %s",
                bucket_name,
                object_name,
                exc,
            )
        else:
            if remote_head and _is_remote_object_current(
                remote_head=remote_head,
                source_digest=source_digest,
                expected_length=source_digest.size,
                content_type=content_type,
            ):
                return False

    extra_args: dict[str, Any] = {
        "Metadata": source_digest.as_metadata(),
    }
    if content_type is not None:
        extra_args["ContentType"] = content_type

    with file_path.open("rb") as handle:
        try:
            s3_client.upload_fileobj(
                handle,
                bucket_name,
                object_name,
                ExtraArgs=extra_args,
                Callback=callback,
            )
        except ClientError as exc:
            raise _create_r2_operation_error(exc, s3_client, bucket_name, object_name) from exc

    return True
