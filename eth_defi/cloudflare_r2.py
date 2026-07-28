"""Cloudflare R2 upload helpers.

These helpers add cheap ``head_object()``-based change detection for
uploads. By storing source checksums in S3 object metadata, callers can
skip unchanged uploads without downloading the remote object body.
"""

from __future__ import annotations

import hashlib
import logging
import time
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

#: Default browser/CDN cache time for public R2 uploads.
R2_DEFAULT_CACHE_CONTROL = "public, max-age=3600"

#: HTTP status code returned by S3-compatible APIs when access is forbidden.
R2_HTTP_STATUS_FORBIDDEN = 403

#: HTTP status code returned by S3-compatible APIs for conflict responses.
R2_HTTP_STATUS_CONFLICT = 409

#: Short access key IDs are left unmasked because masking would hide everything.
R2_UNMASKED_ACCESS_KEY_ID_MAX_LENGTH = 8


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


@dataclass(slots=True, frozen=True)
class R2HeadObjectRetry:
    """Retry policy for R2 ``HeadObject`` metadata reads.

    ``fetch_r2_object_head()`` applies this policy to retry transient
    ``409 Conflict`` responses. Persistent conflicts still surface as
    enriched ``R2ConflictError`` exceptions, which subclass
    ``R2OperationError`` for existing broad handlers.

    The default policy waits before falling back to the caller's error
    handling. Upload helpers therefore pay the full retry budget before
    deciding that the skip-if-current pre-flight check failed and that
    the actual upload should be attempted anyway.
    """

    #: Maximum number of ``HeadObject`` attempts.
    max_attempts: int = 3

    #: Delay before the first retry, in seconds.
    initial_delay_seconds: float = 1.0

    #: Exponential backoff multiplier between retries.
    backoff: float = 2.0

    def validate(self) -> None:
        """Validate retry policy values.

        The retry loop calls this before touching R2 so invalid caller
        configuration fails with a direct local error instead of a later
        ``time.sleep()`` failure or a confusing retry schedule.

        :return:
            None.
        """
        if self.max_attempts < 1:
            message = "retry.max_attempts must be at least one"
            raise ValueError(message)

        if self.initial_delay_seconds < 0:
            message = "retry.initial_delay_seconds must be non-negative"
            raise ValueError(message)

        if self.backoff <= 0:
            message = "retry.backoff must be positive"
            raise ValueError(message)


#: Default retry policy for R2 ``HeadObject`` metadata reads.
R2_HEAD_OBJECT_RETRY = R2HeadObjectRetry()


class R2OperationError(RuntimeError):
    """Raised when an R2 operation fails with enriched diagnostics."""


class R2AccessDeniedError(R2OperationError):
    """Raised when R2 rejects an operation because access is denied."""


class R2ConflictError(R2OperationError):
    """Raised when R2 reports a conflict for an object operation."""


def _mask_access_key_id(access_key_id: str | None) -> str:
    """Mask an access key ID for safe logging.

    :param access_key_id:
        Raw access key ID from the boto3 client credentials.

    :return:
        Masked access key ID safe to emit in logs.
    """
    if not access_key_id:
        return "<unknown>"
    if len(access_key_id) <= R2_UNMASKED_ACCESS_KEY_ID_MAX_LENGTH:
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

    if error_code in {"403", "AccessDenied", "Forbidden"} or http_status == R2_HTTP_STATUS_FORBIDDEN:
        detail_lines.append("Likely causes: wrong R2 access key ID; wrong R2 secret access key; wrong Cloudflare account ID in the endpoint URL; wrong bucket name; or missing read/write permission for this bucket.")
        detail_lines.append(f"Original R2 error message: {error_message}")
        return R2AccessDeniedError(" ".join(detail_lines))

    if error_code in {"409", "Conflict"} or http_status == R2_HTTP_STATUS_CONFLICT:
        detail_lines.append("Likely cause: R2 reported an object conflict. For HeadObject pre-flight checks, retry or attempt the upload operation itself.")
        detail_lines.append(f"Original R2 error message: {error_message}")
        return R2ConflictError(" ".join(detail_lines))

    detail_lines.append(f"Original R2 error message: {error_message}")
    return R2OperationError(" ".join(detail_lines))


def copy_r2_object_daily_backup(
    s3_client: Any,
    bucket_name: str,
    source_key: str,
    *,
    backup_prefix: str = "daily",
) -> bool:
    """Create a daily timestamped backup copy of an R2 object.

    Uses server-side ``copy_object()`` to create a dated snapshot within
    the same bucket. The copy is skipped if a backup for today already
    exists (cheap ``head_object()`` pre-flight check).

    Failures are **non-fatal** — any ``ClientError`` is logged as a
    warning and the function returns ``False``. The caller's primary
    upload has already succeeded, so a backup copy failure should not
    crash the pipeline.

    Backup key layout::

        {backup_prefix} / {YYYY - MM - DD} / {source_key}

    The full ``source_key`` is preserved (including any upload prefix)
    to avoid collisions.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param bucket_name:
        Bucket containing the source object (backup is created in the
        same bucket).

    :param source_key:
        Object key of the live file to back up.

    :param backup_prefix:
        Top-level prefix for backup copies. Defaults to ``daily``,
        making it easy to target with R2 lifecycle rules.

    :return:
        ``True`` if a backup copy was created, ``False`` if it was
        skipped (already exists) or if the copy failed.
    """
    from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415

    from eth_defi.compat import native_datetime_utc_now  # noqa: PLC0415

    today = native_datetime_utc_now().strftime("%Y-%m-%d")
    backup_key = f"{backup_prefix}/{today}/{source_key}"

    try:
        head = fetch_r2_object_head(s3_client, bucket_name, backup_key)
        if head is not None:
            logger.info(
                "Daily backup already exists, skipping: s3://%s/%s",
                bucket_name,
                backup_key,
            )
            return False
    except R2OperationError:
        logger.warning(
            "Daily backup head check failed for s3://%s/%s, attempting copy anyway",
            bucket_name,
            backup_key,
        )

    try:
        s3_client.copy_object(
            Bucket=bucket_name,
            Key=backup_key,
            CopySource={"Bucket": bucket_name, "Key": source_key},
        )
    except ClientError as exc:
        enriched = _create_r2_operation_error(exc, s3_client, bucket_name, backup_key)
        logger.warning(
            "Daily backup copy failed for s3://%s/%s: %s",
            bucket_name,
            backup_key,
            enriched,
        )
        return False
    except BotoCoreError as exc:
        logger.warning(
            "Daily backup copy failed for s3://%s/%s: %s",
            bucket_name,
            backup_key,
            exc,
        )
        return False

    logger.info(
        "Created daily backup: s3://%s/%s -> s3://%s/%s",
        bucket_name,
        source_key,
        bucket_name,
        backup_key,
    )
    return True


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
    *,
    retry: R2HeadObjectRetry = R2_HEAD_OBJECT_RETRY,
) -> dict[str, Any] | None:
    """Fetch object metadata using ``head_object()``.

    Missing objects return ``None``. Any other R2 error is re-raised as
    an enriched runtime exception with Cloudflare-specific diagnostics,
    while preserving the original botocore exception as the nested
    cause.

    Cloudflare R2 has returned HTTP ``409 Conflict`` for a
    ``HeadObject`` request in production. The exact R2-side cause is
    unknown. This helper treats that response as retryable because
    ``HeadObject`` is commonly used here as a read-side optimisation
    before an upload. If the retry budget is exhausted, this function
    raises ``R2ConflictError``. Upload helpers that use ``HeadObject``
    only for ``skip_if_current`` catch that conflict and proceed to the
    write operation, letting ``PutObject`` or ``upload_fileobj()`` provide
    the final result.

    This retry exists because the production vault scanner observed
    ``HeadObject`` returning ``409 Conflict`` for
    ``vault-protocol-metadata/frax-finance/metadata.json`` on
    2026-07-28, aborting the protocol metadata export before any upload
    attempt was made. Cloudflare documents ``HeadObject`` as implemented
    in its S3-compatible API:
    https://developers.cloudflare.com/r2/api/s3/api/#object-level-operations.
    Cloudflare's R2 error reference documents S3-compatible error
    responses:
    https://developers.cloudflare.com/r2/api/error-codes/.
    Amazon S3's canonical ``HeadObject`` documentation notes that
    ``HEAD`` failures can be generic because the response has no body:
    https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param bucket_name:
        Target R2 bucket name.

    :param object_name:
        Object key inside the bucket.

    :param retry:
        Retry policy for transient ``409 Conflict`` responses.

    :return:
        ``head_object()`` response, or ``None`` if the object does not
        exist.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    retry.validate()

    attempt = 1
    retry_delay_seconds = retry.initial_delay_seconds

    while True:
        try:
            return s3_client.head_object(Bucket=bucket_name, Key=object_name)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")

            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None

            # R2 sometimes returns either a numeric S3 code ("409") or a
            # named S3 code ("Conflict") for this transient condition.
            # Treat both representations, plus the raw HTTP status, as
            # the same retryable metadata-read conflict.
            is_retryable_conflict = error_code in {"409", "Conflict"} or http_status == R2_HTTP_STATUS_CONFLICT
            has_retry_budget = attempt < retry.max_attempts

            if is_retryable_conflict and has_retry_budget:
                logger.warning(
                    "R2 HeadObject returned a retryable conflict for s3://%s/%s on attempt %d/%d. Retrying in %.2f seconds.",
                    bucket_name,
                    object_name,
                    attempt,
                    retry.max_attempts,
                    retry_delay_seconds,
                )
                time.sleep(retry_delay_seconds)
                retry_delay_seconds *= retry.backoff
                attempt += 1
                continue

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
    cache_control: str | None = R2_DEFAULT_CACHE_CONTROL,
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

    :param cache_control:
        Expected Cache-Control header, if relevant for this upload.

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

    if cache_control is not None and remote_head.get("CacheControl") != cache_control:
        return False

    metadata = {key.lower(): value for key, value in (remote_head.get("Metadata") or {}).items()}

    if metadata.get(R2_SOURCE_SHA256_METADATA_KEY) == source_digest.sha256 and metadata.get(R2_SOURCE_SIZE_METADATA_KEY) == str(source_digest.size):
        return True

    etag = str(remote_head.get("ETag", "")).strip('"')
    if payload_md5 and etag and "-" not in etag and etag == payload_md5:
        return True

    return False


def _log_r2_head_preflight_failure(
    bucket_name: str,
    object_name: str,
    exc: R2OperationError,
) -> None:
    """Log a failed R2 ``HeadObject`` upload pre-flight check.

    Upload helpers use ``HeadObject`` only as an optimisation to skip
    unchanged payloads. When the pre-flight check fails with a known
    recoverable class, the caller should still attempt the upload and let
    the write operation provide the final answer.

    :param bucket_name:
        Target R2 bucket name.

    :param object_name:
        Destination object key.

    :param exc:
        Enriched R2 exception raised by the pre-flight ``HeadObject``.

    :return:
        None.
    """
    logger.warning(
        "R2 HeadObject pre-flight failed for s3://%s/%s while checking whether upload can be skipped. Proceeding with upload attempt anyway. %s",
        bucket_name,
        object_name,
        exc,
    )


def upload_bytes_to_r2(
    s3_client: Any,
    payload: bytes,
    bucket_name: str,
    object_name: str,
    *,
    content_type: str | None = None,
    content_encoding: str | None = None,
    cache_control: str | None = R2_DEFAULT_CACHE_CONTROL,
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

    :param cache_control:
        Optional Cache-Control header for the upload.

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
        except (R2AccessDeniedError, R2ConflictError) as exc:
            # The pre-flight ``HEAD`` only avoids unnecessary uploads.
            # If that optimisation is blocked by a known recoverable
            # read-side failure, attempt the write and let R2 decide it.
            _log_r2_head_preflight_failure(bucket_name, object_name, exc)
        else:
            if remote_head and _is_remote_object_current(
                remote_head=remote_head,
                source_digest=source_digest,
                expected_length=len(payload),
                content_type=content_type,
                content_encoding=content_encoding,
                cache_control=cache_control,
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
    if cache_control is not None:
        put_kwargs["CacheControl"] = cache_control

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
    cache_control: str | None = R2_DEFAULT_CACHE_CONTROL,
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

    :param cache_control:
        Optional Cache-Control header for the upload.

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
        except (R2AccessDeniedError, R2ConflictError) as exc:
            # The pre-flight ``HEAD`` only avoids unnecessary uploads.
            # If that optimisation is blocked by a known recoverable
            # read-side failure, attempt the write and let R2 decide it.
            _log_r2_head_preflight_failure(bucket_name, object_name, exc)
        else:
            if remote_head and _is_remote_object_current(
                remote_head=remote_head,
                source_digest=source_digest,
                expected_length=source_digest.size,
                content_type=content_type,
                cache_control=cache_control,
            ):
                return False

    extra_args: dict[str, Any] = {
        "Metadata": source_digest.as_metadata(),
    }
    if content_type is not None:
        extra_args["ContentType"] = content_type
    if cache_control is not None:
        extra_args["CacheControl"] = cache_control

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
