"""Publish the small vault scan receipt consumed by live readiness checks.

The scanner writes the manifest only after the cleaned price parquet has been
uploaded. It contains per-chain scan provenance and the maximum timestamp from
that exact cleaned snapshot, allowing a live executor to poll a small JSON file
without repeatedly downloading hundreds of megabytes of parquet data.

The object is published to the existing private alternative-vault bucket named
by ``R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME``. Its key is
``{UPLOAD_PREFIX}vault-scan-manifest.json`` alongside the cleaned price object;
the upload explicitly sets ``application/json`` and ``Cache-Control: no-store``.
The serving worker and CDN must also honour that policy; R2 metadata alone
cannot prove that a readiness poll will bypass every intermediate cache.

See `Cloudflare R2 object metadata <https://developers.cloudflare.com/r2/api/s3/api/>`__
for the S3-compatible HEAD and upload operations used here. Operational details
and the consumer contract are documented in ``docs/README-vault-scan-manifest.md``.
"""

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal, TypedDict

import pyarrow.parquet as pq

from eth_defi.chain import get_chain_name
from eth_defi.cloudflare_r2 import create_r2_client, fetch_r2_object_head, upload_bytes_to_r2
from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


class VaultPriceFileManifest(TypedDict):
    """Bind freshness claims to the private price object the consumer downloads.

    Filled by :func:`build_vault_scan_manifest` from the publisher's R2 HEAD
    response. Consumers compare the opaque ETag, including multipart suffixes,
    with the download response; it is not a content hash they must recompute.
    """

    #: Exact private R2 object key, retained for audit only.
    key: str

    #: Strong opaque ETag without HTTP quote characters.
    etag: str


class VaultChainScanManifest(TypedDict):
    """Separate successful collection from the newest published observation.

    The builder combines scanner provenance with cleaned parquet maxima. A
    metadata-only scan cannot advance collection time, and a completed price
    scan need not have received new candles. Both fields are UTC ISO-8601
    strings ending in ``Z``, or ``None`` when unknown.
    """

    #: Human-readable label; consumers select chains by numeric key.
    name: str

    #: Completion time of the last successful price fetch, or ``None`` if unknown.
    last_successful_price_scan_ended_at: str | None

    #: Maximum timestamp in the published cleaned file, including hourly bucket
    #: labels from sparse observations. This is not per-vault completeness.
    last_candle_at: str | None


class VaultScanManifest(TypedDict):
    """Wire contract polled before a live strategy downloads its universe.

    :func:`publish_vault_scan_manifest` serialises this mapping as JSON after
    private price export. The matching consumer is
    ``tradingstrategy.vault_scan_manifest.VaultScanManifest``. Chain-wide
    freshness is a scheduling hint, not a guarantee that every vault has data.
    """

    #: Wire schema version; the consumer currently supports version 1 only.
    schema_version: Literal[1]

    #: UTC publication time after the referenced price upload.
    published_at: str

    #: Identity and source version of the referenced cleaned price object.
    price_file: VaultPriceFileManifest

    #: Decimal chain-ID keys and their published freshness metadata.
    chains: dict[str, VaultChainScanManifest]


def _format_utc(timestamp: datetime.datetime) -> str:
    """Normalise producer timestamps to the consumer's UTC wire format.

    Used for both persisted scan provenance and Arrow price timestamps so
    their timezone representations do not change readiness comparisons.

    :param timestamp: Naive UTC or timezone-aware instant.
    :return: ISO-8601 UTC timestamp with a trailing ``Z``.
    """

    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    value = timestamp.isoformat(timespec="microseconds").rstrip("0").rstrip(".")
    return f"{value}Z"


def _load_price_scan_state(path: Path) -> dict[str, str | None]:
    """Read the scanner's separate price-success state for manifest creation.

    A first run without persisted provenance produces unknown scan times,
    never inferred successes. Support the scanner's ``items`` wrapper and a
    plain mapping used by manual producers.

    :param path: Local JSON written by the scanner's price-success callback.
    :return: Decimal chain-ID keys mapped to UTC timestamp strings or ``None``.
    """

    if not path.exists():
        return {}
    document = json.loads(path.read_text())
    items = document.get("items", document)
    if not isinstance(items, dict):
        raise ValueError(f"Price scan state at {path} must contain an object")
    normalised: dict[str, str | None] = {}
    for name, value in items.items():
        if not re.fullmatch(r"[1-9][0-9]*", str(name)):
            raise ValueError(f"Invalid chain ID {name!r} in price scan state at {path}")
        if value is None:
            normalised[str(name)] = None
        elif isinstance(value, str):
            timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            normalised[str(name)] = _format_utc(timestamp)
        else:
            raise ValueError(f"Invalid price scan timestamp for chain {name} at {path}")
    return normalised


def build_vault_scan_manifest(
    cleaned_price_path: Path,
    price_scan_state_path: Path,
    price_object_key: str,
    price_etag: str,
    published_at: datetime.datetime,
) -> VaultScanManifest:
    """Build a v1 manifest from the exact cleaned parquet being published.

    Called by :func:`publish_vault_scan_manifest` after private price export.
    Only ``chain`` (numeric chain ID) and ``timestamp`` (Arrow timestamp) are
    loaded; aggregation stays in Arrow to avoid materialising millions of
    historical rows as Python objects. Sparse four-hour observations remain
    valid inputs: no artificial hourly completeness requirement is applied.

    :param cleaned_price_path:
        Cleaned parquet snapshot that was uploaded before this call.
    :param price_scan_state_path:
        Local price-only provenance state written after successful scans.
    :param price_object_key:
        R2 key of the uploaded parquet.
    :param price_etag:
        Strong ETag read from the uploaded object.
    :param published_at:
        UTC time immediately before manifest publication.
    :return:
        JSON-serialisable manifest mapping.
    """

    price_etag = price_etag.strip('"')
    if not price_etag or price_etag.startswith("W/") or '"' in price_etag:
        raise ValueError(f"Expected a strong opaque price ETag, got {price_etag!r}")
    table = pq.read_table(cleaned_price_path, columns=["chain", "timestamp"])
    # Aggregate in Arrow: the cleaned history contains millions of hourly
    # rows, but the receipt needs only one timestamp per chain. Sparse native
    # vault observations do not require an hourly sample-count threshold.
    maxima = table.group_by("chain").aggregate([("timestamp", "max")])
    latest_by_chain = {str(int(row["chain"])): row["timestamp_max"] for row in maxima.to_pylist() if row["chain"] is not None and row["timestamp_max"] is not None}

    scan_state = _load_price_scan_state(price_scan_state_path)
    chains: dict[str, VaultChainScanManifest] = {}
    for chain_key in sorted(set(latest_by_chain) | set(scan_state)):
        latest = latest_by_chain.get(chain_key)
        try:
            name = get_chain_name(int(chain_key))
        except (KeyError, ValueError):
            name = chain_key
        chains[chain_key] = {
            "name": name,
            "last_successful_price_scan_ended_at": scan_state.get(chain_key),
            "last_candle_at": _format_utc(latest) if latest is not None else None,
        }
    return {
        "schema_version": 1,
        "published_at": _format_utc(published_at),
        "price_file": {"key": price_object_key, "etag": price_etag.strip('"')},
        "chains": chains,
    }


def publish_vault_scan_manifest(
    cleaned_price_path: Path,
    price_scan_state_path: Path,
    *,
    published_at: datetime.datetime | None = None,
) -> bool:
    """Upload the manifest to the private vault-data R2 bucket.

    The price object must already be uploaded. The manifest uses ``no-store``
    so the authenticated JSON endpoint can be polled without a stale edge or
    client cache. Existing unrelated data-file cache policy is unchanged.
    Called by :func:`eth_defi.vault.post_processing.run_post_processing` after
    successful export, under the scanner's existing single-writer lock.

    :param cleaned_price_path:
        Exact cleaned parquet snapshot that was uploaded.
    :param price_scan_state_path:
        Local price-only scan provenance written by the scanner.
    :param published_at:
        Optional publication timestamp; defaults to the current UTC time.

    :return:
        ``True`` after publication, ``False`` when the local price file is
        absent. Missing remote objects or configuration raise an error.
    """

    if not cleaned_price_path.is_file():
        logger.warning("Skipping vault scan manifest: local price file missing: %s", cleaned_price_path)
        return False
    bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    if not all((bucket_name, access_key_id, secret_access_key, endpoint_url)):
        message = "Private R2 configuration is required to publish vault scan manifest"
        raise RuntimeError(message)

    prefix = os.environ.get("UPLOAD_PREFIX", "")
    price_object_key = f"{prefix}{cleaned_price_path.name}"
    manifest_key = f"{prefix}vault-scan-manifest.json"
    client = create_r2_client(endpoint_url=endpoint_url, access_key_id=access_key_id, secret_access_key=secret_access_key)
    price_head = fetch_r2_object_head(client, bucket_name, price_object_key)
    if not price_head or not price_head.get("ETag"):
        raise RuntimeError(f"Uploaded price object has no readable ETag: s3://{bucket_name}/{price_object_key}")
    manifest = build_vault_scan_manifest(
        cleaned_price_path=cleaned_price_path,
        price_scan_state_path=price_scan_state_path,
        price_object_key=price_object_key,
        price_etag=str(price_head["ETag"]).strip('"'),
        published_at=published_at or native_datetime_utc_now(),
    )
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    upload_bytes_to_r2(
        s3_client=client,
        payload=payload,
        bucket_name=bucket_name,
        object_name=manifest_key,
        content_type="application/json",
        cache_control="no-store",
    )
    logger.info("Published vault scan manifest for %d chains to s3://%s/%s", len(manifest["chains"]), bucket_name, manifest_key)
    return True
