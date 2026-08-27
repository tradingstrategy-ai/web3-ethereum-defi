"""Publish the private flat-key crypto-vaults bundle to Cloudflare R2.

Uses the `Cloudflare R2 S3 API <https://developers.cloudflare.com/r2/api/s3/>`__.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import brotli
import pandas as pd
from atomicwrites import atomic_write

from eth_defi.cloudflare_r2 import copy_r2_object_daily_backup, create_r2_client, upload_file_to_r2
from eth_defi.vault.crypto_vaults import CryptoVaultPaths
from eth_defi.vault.denomination import CRYPTO_DENOMINATION_FAMILY_NAMES, get_denomination_whitelist_digest

#: Private-bundle identifier stored in metadata and manifest documents.
CRYPTO_VAULTS_BUNDLE_NAME = "crypto-vaults"

logger = logging.getLogger(__name__)


def _get_r2_configuration() -> tuple[str, str, str, str]:
    """Resolve private R2 configuration from the established environment names.

    :return:
        Alternative bucket name, endpoint URL, access key ID and secret key.
    """
    bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    missing = [
        label
        for label, value in {
            "R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME": bucket_name,
            "R2_DATA_ENDPOINT_URL or R2_VAULT_METADATA_ENDPOINT_URL": endpoint_url,
            "R2_DATA_ACCESS_KEY_ID or R2_VAULT_METADATA_ACCESS_KEY_ID": access_key_id,
            "R2_DATA_SECRET_ACCESS_KEY or R2_VAULT_METADATA_SECRET_ACCESS_KEY": secret_access_key,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Crypto vault R2 export is not configured: {', '.join(missing)}")
    return bucket_name, endpoint_url, access_key_id, secret_access_key


def _write_brotli_metadata(metadata_path: Path) -> Path:
    """Create the conventional Brotli sidecar for one metadata document.

    :param metadata_path:
        Uncompressed metadata JSON file.
    :return:
        Brotli sidecar path.
    """
    target = metadata_path.with_suffix(metadata_path.suffix + ".br")
    compressed = brotli.compress(metadata_path.read_bytes())
    with atomic_write(str(target), mode="wb", overwrite=True) as output:
        output.write(compressed)
    return target


def _get_payload_paths(paths: CryptoVaultPaths, *, include_manifest: bool = False) -> tuple[tuple[str, Path], ...]:
    """Return payload object names and local paths in publication order.

    :param paths:
        Local bundle paths.
    :param include_manifest:
        Append the manifest, which must always be published last.
    :return:
        Ordered local payload definitions.
    """
    payloads = (
        (paths.cleaned_price_path.name, paths.cleaned_price_path),
        (paths.metadata_path.name, paths.metadata_path),
        (paths.metadata_path.with_suffix(".json.br").name, paths.metadata_path.with_suffix(".json.br")),
        (paths.sticky_state_path.name, paths.sticky_state_path),
    )
    return (*payloads, (paths.manifest_path.name, paths.manifest_path)) if include_manifest else payloads


def build_crypto_vault_manifest(paths: CryptoVaultPaths, metadata: dict[str, Any]) -> dict[str, Any]:
    """Build the current flat-key bundle manifest from local payloads.

    :param paths:
        Local bundle paths.
    :param metadata:
        Generated metadata document used for family/row counts.
    :return:
        JSON-serialisable manifest document.
    """
    payloads = _get_payload_paths(paths)
    files = {}
    for object_name, path in payloads:
        body = path.read_bytes()
        files[object_name] = {
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    family_counts = {family: sum(1 for vault in metadata["vaults"] if vault["denomination_family"] == family) for family in CRYPTO_DENOMINATION_FAMILY_NAMES}
    prices_df = pd.read_parquet(paths.cleaned_price_path, columns=["denomination_family", "timestamp"])
    # Pandas restores a Parquet ``timestamp`` index as an index, whereas
    # hand-crafted or legacy files can retain it as a regular column.
    timestamps = prices_df.index if isinstance(prices_df.index, pd.DatetimeIndex) else pd.to_datetime(prices_df["timestamp"])
    price_row_counts = {family: int((prices_df["denomination_family"] == family).sum()) for family in CRYPTO_DENOMINATION_FAMILY_NAMES}
    return {
        "bundle": CRYPTO_VAULTS_BUNDLE_NAME,
        "schema_version": 1,
        "generated_at": metadata["generated_at"],
        "metadata": metadata["metadata"],
        "denomination_whitelist_sha256": get_denomination_whitelist_digest(),
        "files": files,
        "vault_counts": family_counts,
        "vault_count_total": sum(family_counts.values()),
        "price_row_counts": price_row_counts,
        "price_row_count_total": sum(price_row_counts.values()),
        "price_observation_range": {
            "min_timestamp": timestamps.min().isoformat() if not prices_df.empty else None,
            "max_timestamp": timestamps.max().isoformat() if not prices_df.empty else None,
        },
        "threshold_usd_guideline": metadata["threshold_usd_guideline"],
        "fixed_usd_rates": metadata["fixed_usd_rates"],
        "sampling": "sparse daily observations; metrics use forward-filled calendar-day prices",
    }


def publish_crypto_vault_bundle(paths: CryptoVaultPaths, metadata: dict[str, Any]) -> bool:
    """Upload crypto payloads, manifest and daily backups to private R2.

    Payload keys use flat root names prefixed with ``crypto-`` so they share the
    public bundle's layout without colliding with it. The manifest is uploaded
    last and consumers must verify its digests before accepting the bundle.

    :param paths:
        Local bundle paths.
    :param metadata:
        Metadata document generated by the bundle builder.
    :return:
        ``True`` when all uploads complete; exceptions propagate to the guarded
        post-processing phase.
    """
    bucket_name, endpoint_url, access_key_id, secret_access_key = _get_r2_configuration()
    logger.info("Publishing %s bundle to private R2 bucket %s", CRYPTO_VAULTS_BUNDLE_NAME, bucket_name)
    _write_brotli_metadata(paths.metadata_path)
    manifest = build_crypto_vault_manifest(paths, metadata)
    with atomic_write(str(paths.manifest_path), mode="w", overwrite=True, encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, ensure_ascii=False, allow_nan=False)

    client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    prefix = os.environ.get("UPLOAD_PREFIX", "")
    uploaded_keys: list[str] = []
    for object_name, path in _get_payload_paths(paths, include_manifest=True):
        object_key = f"{prefix}{object_name}"
        uploaded = upload_file_to_r2(
            s3_client=client,
            file_path=path,
            bucket_name=bucket_name,
            object_name=object_key,
            skip_if_current=True,
        )
        uploaded_keys.append(object_key)
        logger.info("%s %s", "Uploaded" if uploaded else "Skipped unchanged", object_key)

    if os.environ.get("R2_DAILY_BACKUP", "true").lower() != "false":
        for object_key in uploaded_keys:
            copy_r2_object_daily_backup(client, bucket_name, object_key)
    return True
