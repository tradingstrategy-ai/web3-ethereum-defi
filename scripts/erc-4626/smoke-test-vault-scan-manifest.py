"""Create and verify a production-data vault scan manifest in Cloudflare R2.

The smoke test builds a manifest from the local cleaned price Parquet and
price-scan state used by the live scanner. It reads the matching private price
object ETag from R2, uploads a uniquely named receipt below ``smoke-tests/``,
and reads that receipt back to validate its content and object metadata.

It deliberately does not call the production publisher with its normal key:
doing so would replace the readiness receipt consumed by live trading. The
test object is retained in R2 for audit and is not mapped to the authenticated
manifest endpoint.

The upload and readback use the `Cloudflare R2 S3-compatible API
<https://developers.cloudflare.com/r2/api/s3/>`__.

Usage:

.. code-block:: shell

    source .local-test.env && \
      poetry run python scripts/erc-4626/smoke-test-vault-scan-manifest.py

Environment variables:

- ``PIPELINE_DATA_DIR``: Local pipeline data directory. Defaults to
  ``~/.tradingstrategy/vaults``.
- ``CLEANED_PRICE_DATABASE``: Cleaned price Parquet override. Defaults to
  ``$PIPELINE_DATA_DIR/cleaned-vault-prices-1h.parquet``.
- ``PRICE_SCAN_STATE_DATABASE``: Price-scan provenance override. Defaults to
  ``$PIPELINE_DATA_DIR/vault-price-scan-state.json``.
- ``MANIFEST_SMOKE_PRICE_OBJECT_KEY``: Existing private price object key. The
  default is ``${UPLOAD_PREFIX}cleaned-vault-prices-1h.parquet``.
- ``MANIFEST_SMOKE_OBJECT_KEY``: Optional R2 test object key. It must begin
  with ``smoke-tests/``; by default the script creates a unique key there.
- ``MANIFEST_SMOKE_BUCKET_NAME``: Optional explicit private test bucket. Falls
  back to ``R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME``.
- ``R2_DATA_ACCESS_KEY_ID``, ``R2_DATA_SECRET_ACCESS_KEY`` and
  ``R2_DATA_ENDPOINT_URL``: Private data credentials. Each falls back to the
  corresponding ``R2_VAULT_METADATA_*`` value.
"""

import datetime
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tabulate import tabulate

from eth_defi.cloudflare_r2 import create_r2_client, fetch_r2_object_head, upload_bytes_to_r2
from eth_defi.compat import native_datetime_utc_now
from eth_defi.utils import setup_console_logging
from eth_defi.vault.scan_manifest import VaultScanManifest, build_vault_scan_manifest
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class R2Configuration:
    """Private R2 destination and authentication required for this test."""

    #: Private R2 bucket storing the complete vault dataset.
    bucket_name: str

    #: R2 access key with permission to read and write this bucket.
    access_key_id: str

    #: Secret paired with :attr:`access_key_id`.
    secret_access_key: str

    #: Account-specific R2 S3-compatible endpoint.
    endpoint_url: str


def _resolve_local_paths() -> tuple[Path, Path]:
    """Resolve the local cleaned snapshot and scan-provenance state paths.

    The default paths are exactly those used by the production scanner, while
    allowing an operator to point the smoke test at a retained copy.

    :return:
        Tuple containing the cleaned price Parquet and price-scan state JSON
        paths. The Parquet file is guaranteed to exist; state may be absent on
        a first scanner run because that is a valid manifest input.
    """

    data_dir = get_pipeline_data_dir()
    cleaned_path = Path(os.environ.get("CLEANED_PRICE_DATABASE", data_dir / "cleaned-vault-prices-1h.parquet")).expanduser()
    state_path = Path(os.environ.get("PRICE_SCAN_STATE_DATABASE", data_dir / "vault-price-scan-state.json")).expanduser()
    if not cleaned_path.is_file():
        raise FileNotFoundError(f"Cleaned price Parquet does not exist: {cleaned_path}")
    return cleaned_path, state_path


def _resolve_r2_configuration() -> R2Configuration:
    """Load the configured private R2 bucket and credentials.

    The default bucket and credential fallback order match the production
    private publisher. An explicit smoke bucket makes the command runnable
    from local secret files that deliberately omit production deployment
    variables without weakening the ``smoke-tests/`` object-key guard.

    :return:
        Private R2 connection configuration.
    """

    bucket_name = os.environ.get("MANIFEST_SMOKE_BUCKET_NAME") or os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    missing = [
        name
        for name, value in {
            "MANIFEST_SMOKE_BUCKET_NAME or R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME": bucket_name,
            "R2_DATA_ACCESS_KEY_ID or R2_VAULT_METADATA_ACCESS_KEY_ID": access_key_id,
            "R2_DATA_SECRET_ACCESS_KEY or R2_VAULT_METADATA_SECRET_ACCESS_KEY": secret_access_key,
            "R2_DATA_ENDPOINT_URL or R2_VAULT_METADATA_ENDPOINT_URL": endpoint_url,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Vault manifest smoke test needs private R2 configuration: {', '.join(missing)}")
    return R2Configuration(
        bucket_name=bucket_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        endpoint_url=endpoint_url,
    )


def _resolve_smoke_object_key(published_at: datetime.datetime) -> str:
    """Create a safe, unique R2 key for the receipt uploaded by this test.

    An explicit key is accepted for reproducible investigations, but the
    namespace guard prevents this manual command from replacing the live
    readiness object or a served test prefix accidentally.

    :param published_at:
        UTC time used for this manifest publication.
    :return:
        Unique R2 object key under ``smoke-tests/``.
    """

    object_key = os.environ.get("MANIFEST_SMOKE_OBJECT_KEY")
    if object_key is None:
        timestamp = published_at.strftime("%Y%m%dT%H%M%SZ")
        object_key = f"smoke-tests/vault-scan-manifest/{timestamp}-{uuid.uuid4().hex[:8]}.json"
    if not object_key.startswith("smoke-tests/"):
        raise ValueError(f"MANIFEST_SMOKE_OBJECT_KEY must begin with 'smoke-tests/': {object_key!r}")
    return object_key


def fetch_remote_manifest(s3_client: Any, bucket_name: str, object_key: str) -> tuple[dict[str, Any], bytes]:
    """Fetch a smoke-test object body and its final R2 metadata.

    The readback makes the manual command an end-to-end integration test:
    successful ``put_object`` alone would not verify the stored JSON or the
    cache and content-type metadata that clients require.

    :param s3_client:
        Authenticated S3-compatible R2 client.
    :param bucket_name:
        Private R2 bucket name.
    :param object_key:
        Newly uploaded smoke-test object key.
    :return:
        R2 HEAD response and exact downloaded object bytes.
    """

    head = fetch_r2_object_head(s3_client, bucket_name, object_key)
    if head is None:
        raise RuntimeError(f"R2 did not retain uploaded smoke-test object: s3://{bucket_name}/{object_key}")
    response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    body = response["Body"]
    try:
        payload = body.read()
    finally:
        body.close()
    return head, payload


def _validate_readback(
    manifest: VaultScanManifest,
    payload: bytes,
    *,
    price_object_key: str,
    price_etag: str,
    remote_head: dict[str, Any],
    remote_payload: bytes,
) -> None:
    """Validate the uploaded receipt against local and remote expectations.

    The checks deliberately cover the exact Parquet key and opaque ETag that
    bind the receipt to the local snapshot, then verify that R2 returned the
    bytes and no-store metadata needed by the authenticated polling route.

    :param manifest:
        Locally built JSON-serialisable manifest.
    :param payload:
        Exact JSON bytes supplied to R2.
    :param price_object_key:
        Existing private cleaned-price object key.
    :param price_etag:
        Strong ETag read from that private object.
    :param remote_head:
        Metadata returned by R2 after upload.
    :param remote_payload:
        Exact object bytes downloaded from R2.
    :return:
        ``None`` after all smoke-test assertions pass.
    """

    if not manifest["chains"]:
        message = "Local cleaned price Parquet produced a manifest without chains"
        raise RuntimeError(message)
    if manifest["price_file"] != {"key": price_object_key, "etag": price_etag}:
        message = "Manifest price file identity differs from the private R2 price object"
        raise RuntimeError(message)
    if remote_payload != payload:
        message = "R2 manifest readback differs from the uploaded payload"
        raise RuntimeError(message)
    if json.loads(remote_payload) != manifest:
        message = "R2 manifest JSON readback differs from the locally built manifest"
        raise RuntimeError(message)
    if remote_head.get("ContentType") != "application/json":
        raise RuntimeError(f"R2 manifest has unexpected ContentType: {remote_head.get('ContentType')!r}")
    if remote_head.get("CacheControl") != "no-store":
        raise RuntimeError(f"R2 manifest has unexpected CacheControl: {remote_head.get('CacheControl')!r}")
    if remote_head.get("ContentLength") != len(payload):
        raise RuntimeError(f"R2 manifest has unexpected ContentLength: {remote_head.get('ContentLength')!r}")
    published_at = datetime.datetime.fromisoformat(manifest["published_at"].replace("Z", "+00:00"))
    for chain_id, chain in manifest["chains"].items():
        for field_name in ("last_successful_price_scan_ended_at", "last_candle_at"):
            timestamp = chain[field_name]
            if timestamp is not None and datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")) > published_at:
                message = f"Manifest chain {chain_id} has {field_name} after published_at: {timestamp}"
                raise RuntimeError(message)


def main() -> None:
    """Build, upload and read back a safe production-data manifest receipt.

    This command uses real local scanner artefacts and the configured private
    R2 bucket. It writes one timestamped object below ``smoke-tests/`` and
    never modifies the production readiness manifest or price Parquet.

    :return:
        ``None`` after logging and printing the verified receipt summary.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    cleaned_path, state_path = _resolve_local_paths()
    r2_configuration = _resolve_r2_configuration()
    price_object_key = os.environ.get("MANIFEST_SMOKE_PRICE_OBJECT_KEY", f"{os.environ.get('UPLOAD_PREFIX', '')}{cleaned_path.name}")
    published_at = native_datetime_utc_now()
    smoke_object_key = _resolve_smoke_object_key(published_at)
    s3_client = create_r2_client(
        r2_configuration.endpoint_url,
        r2_configuration.access_key_id,
        r2_configuration.secret_access_key,
    )

    logger.info("Reading private price object identity: s3://%s/%s", r2_configuration.bucket_name, price_object_key)
    price_head = fetch_r2_object_head(s3_client, r2_configuration.bucket_name, price_object_key)
    if price_head is None or not price_head.get("ETag"):
        message = f"Private price object is absent or has no ETag: s3://{r2_configuration.bucket_name}/{price_object_key}"
        raise RuntimeError(message)
    if price_head.get("ContentLength") != cleaned_path.stat().st_size:
        message = f"Local cleaned price Parquet differs in size from the private R2 object: local={cleaned_path.stat().st_size}, remote={price_head.get('ContentLength')}"
        raise RuntimeError(message)
    price_etag = str(price_head["ETag"]).strip('"')
    manifest = build_vault_scan_manifest(
        cleaned_price_path=cleaned_path,
        price_scan_state_path=state_path,
        price_object_key=price_object_key,
        price_etag=price_etag,
        published_at=published_at,
    )
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")

    logger.info("Uploading vault manifest smoke-test receipt: s3://%s/%s", r2_configuration.bucket_name, smoke_object_key)
    upload_bytes_to_r2(
        s3_client=s3_client,
        payload=payload,
        bucket_name=r2_configuration.bucket_name,
        object_name=smoke_object_key,
        content_type="application/json",
        cache_control="no-store",
    )
    remote_head, remote_payload = fetch_remote_manifest(s3_client, r2_configuration.bucket_name, smoke_object_key)
    _validate_readback(
        manifest,
        payload,
        price_object_key=price_object_key,
        price_etag=price_etag,
        remote_head=remote_head,
        remote_payload=remote_payload,
    )

    rows = [
        ["local cleaned price Parquet", cleaned_path],
        ["price-scan state", state_path if state_path.exists() else "absent (valid first-run state)"],
        ["private price object", f"s3://{r2_configuration.bucket_name}/{price_object_key}"],
        ["price ETag", price_etag],
        ["manifest test object", f"s3://{r2_configuration.bucket_name}/{smoke_object_key}"],
        ["chains", len(manifest["chains"])],
        ["manifest bytes", len(payload)],
    ]
    print(tabulate(rows, headers=["Check", "Value"], tablefmt="fancy_grid"))
    logger.info("Vault manifest production-data smoke test passed")


if __name__ == "__main__":
    main()
