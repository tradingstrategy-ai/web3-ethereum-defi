"""Bounded sparkline preparation, rendering and publication orchestration."""

from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import logging
import math
import os
import struct
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import pandas as pd
from atomicwrites import atomic_write
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.cloudflare_r2 import calculate_bytes_digest, create_r2_client, upload_bytes_to_r2
from eth_defi.compat import native_datetime_utc_now
from eth_defi.research.sparkline import (
    MIN_SPARKLINE_HISTORY,
    SPARKLINE_BACKGROUND_COLOR,
    SPARKLINE_GRADIENT_ALPHA,
    SPARKLINE_GRADIENT_TOP_COLOR,
    SPARKLINE_LINE_COLOR,
    SPARKLINE_LINE_WIDTH,
    SPARKLINE_PNG_HEIGHT,
    SPARKLINE_PNG_MARGIN_RATIO,
    SPARKLINE_PNG_WIDTH,
    SPARKLINE_RENDERER_VERSION,
    SPARKLINE_SVG_HEIGHT,
    SPARKLINE_SVG_LINE_WIDTH,
    SPARKLINE_SVG_MARGIN_RATIO,
    SPARKLINE_SVG_WIDTH,
    SparklineData,
    prepare_sparkline_data,
    render_sparkline_png,
    render_sparkline_svg,
)
from eth_defi.vault.denomination import DenominationFamily, classify_denomination, resolve_sparkline_tvl_threshold
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Version of the persisted state envelope.
SPARKLINE_STATE_SCHEMA_VERSION = 1

#: Maximum age of a low-TVL successful completion.
SPARKLINE_LOW_TVL_INTERVAL = datetime.timedelta(days=3)

#: Retain state entries for recently observed vaults.
SPARKLINE_STATE_RETENTION_DAYS = 90

#: Bound image bytes and worker result queues.
SPARKLINE_BATCH_SIZE = 100

#: Number of hexadecimal characters in a SHA-256 digest.
SHA256_HEX_LENGTH = 64

#: Number of output formats required for a complete vault publication.
SPARKLINE_FORMAT_COUNT = 2

#: Supported image families.
SUPPORTED_SPARKLINE_FAMILIES = frozenset(
    {
        DenominationFamily.stablecoin,
        DenominationFamily.eth,
        DenominationFamily.btc,
    }
)


class RenderData(TypedDict):
    """Rendered image payload for one vault and one output format."""

    vault_id: str
    payload: bytes
    content_type: str
    extension: str


@dataclass(slots=True)
class SparklineExportResult:
    """Counters and terminal status from one sparkline export run."""

    success: bool
    counters: dict[str, int]


def _format_timestamp(value: datetime.datetime) -> str:
    """Serialise one naive UTC timestamp with a stable Z suffix."""
    if value.tzinfo is not None:
        raise ValueError(f"Expected naive UTC datetime, got {value!r}")
    return value.replace(microsecond=0).isoformat() + "Z"


def _parse_timestamp(value: Any) -> datetime.datetime | None:
    """Parse one persisted timestamp into a naive UTC datetime."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid sparkline timestamp: {value!r}")
    text = value[:-1] if value.endswith("Z") else value
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _publication_target(bucket_name: str) -> str:
    """Return the non-secret R2 destination identity used by state skips."""
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL", "").rstrip("/")
    return f"{endpoint_url}/{bucket_name}" if endpoint_url else bucket_name


def _validate_state_entry(vault_id: str, entry: Any) -> None:
    """Validate one persisted per-vault state entry without coercion."""
    if not isinstance(entry, dict):
        raise ValueError(f"Sparkline state entry {vault_id!r} is not an object")
    for key in ("last_seen_at", "last_attempted_at", "last_completed_at", "last_uploaded_at", "next_retry_at"):
        if entry.get(key) is not None:
            _parse_timestamp(entry[key])
    input_sha256 = entry.get("input_sha256")
    if input_sha256 is not None:
        if not isinstance(input_sha256, str) or len(input_sha256) != SHA256_HEX_LENGTH or any(character not in "0123456789abcdef" for character in input_sha256):
            raise ValueError(f"Invalid sparkline input digest for {vault_id!r}")
    failures = entry.get("consecutive_failures", 0)
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        raise ValueError(f"Invalid sparkline failure count for {vault_id!r}")
    if "low_tvl" in entry and not isinstance(entry["low_tvl"], bool):
        raise ValueError(f"Invalid sparkline low-TVL flag for {vault_id!r}")
    renderer_version = entry.get("renderer_version")
    if renderer_version is not None and (isinstance(renderer_version, bool) or not isinstance(renderer_version, int) or renderer_version < 1):
        raise ValueError(f"Invalid sparkline renderer version for {vault_id!r}")
    publication_target = entry.get("publication_target")
    if publication_target is not None and (not isinstance(publication_target, str) or not publication_target):
        raise ValueError(f"Invalid sparkline publication target for {vault_id!r}")


def make_empty_sparkline_state(now: datetime.datetime) -> dict[str, Any]:
    """Create an empty versioned sparkline state envelope."""
    return {
        "schema_version": SPARKLINE_STATE_SCHEMA_VERSION,
        "generated_at": _format_timestamp(now),
        "renderer_version": SPARKLINE_RENDERER_VERSION,
        "vaults": {},
    }


def load_sparkline_state(path: Path, now: datetime.datetime | None = None) -> dict[str, Any]:
    """Load and strictly validate the persistent sparkline state."""
    now = now or native_datetime_utc_now()
    if not path.exists():
        return make_empty_sparkline_state(now)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read sparkline state {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SPARKLINE_STATE_SCHEMA_VERSION or isinstance(payload.get("schema_version"), bool):
        raise ValueError(f"Invalid sparkline state schema in {path}")
    renderer_version = payload.get("renderer_version")
    if isinstance(renderer_version, bool) or not isinstance(renderer_version, int) or renderer_version < 1:
        raise ValueError(f"Invalid sparkline renderer version in {path}")
    if not isinstance(payload.get("vaults"), dict):
        raise ValueError(f"Invalid sparkline vault state in {path}")
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError(f"Invalid sparkline generated timestamp in {path}")
    _parse_timestamp(generated_at)
    for vault_id, entry in payload["vaults"].items():
        if not isinstance(vault_id, str):
            message = "Sparkline state vault IDs must be strings"
            raise ValueError(message)
        _validate_state_entry(vault_id, entry)
    return payload


def save_sparkline_state(state: dict[str, Any], path: Path, now: datetime.datetime | None = None) -> None:
    """Prune and atomically persist one sparkline state envelope."""
    now = now or native_datetime_utc_now()
    cutoff = now - datetime.timedelta(days=SPARKLINE_STATE_RETENTION_DAYS)
    retained: dict[str, Any] = {}
    for vault_id, entry in state["vaults"].items():
        last_seen = _parse_timestamp(entry.get("last_seen_at"))
        if last_seen is None or last_seen >= cutoff:
            retained[vault_id] = entry
    state["vaults"] = retained
    state["generated_at"] = _format_timestamp(now)
    state["renderer_version"] = SPARKLINE_RENDERER_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True, encoding="utf-8") as output:
        json.dump(state, output, indent=2, ensure_ascii=False, allow_nan=False)


def _vault_id(row: dict[str, Any]) -> str:
    """Resolve a metadata row's canonical chain-address identifier."""
    detection_data = row.get("_detection_data")
    return detection_data.get_spec().as_string_id()


def _vault_family_map(vault_db: VaultDatabase) -> dict[str, DenominationFamily]:
    """Classify supported metadata vaults by canonical ID."""
    return {_vault_id(row): classify_denomination(row.get("Denomination")) for row in vault_db.rows.values()}


def _vault_symbol_map(vault_db: VaultDatabase) -> dict[str, str | None]:
    """Resolve persisted denomination symbols by canonical ID."""
    return {_vault_id(row): row.get("Denomination") for row in vault_db.rows.values()}


def get_included_vault_ids(vault_db: VaultDatabase, prices_df: pd.DataFrame) -> set[str]:
    """Select supported vaults with finite TVL and sufficient price history.

    :param vault_db:
        Vault metadata rows used for denomination-family classification.
    :param prices_df:
        Naive-UTC price rows containing ``id``, ``share_price`` and
        ``total_assets`` columns.
    :return:
        Canonical IDs with supported denominations, sufficient history and at
        least one finite TVL observation.
    """
    required = {"id", "share_price", "total_assets"}
    missing = required - set(prices_df.columns)
    if missing:
        raise ValueError(f"Sparkline input is missing columns: {sorted(missing)!r}")
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        message = "Sparkline input must have a DatetimeIndex"
        raise ValueError(message)
    families = _vault_family_map(vault_db)
    grouped = prices_df.assign(id=prices_df["id"].astype(str)).groupby("id", sort=False)
    included: set[str] = set()
    for vault_id, group in grouped:
        if families.get(str(vault_id)) not in SUPPORTED_SPARKLINE_FAMILIES:
            continue
        share_prices = pd.to_numeric(group["share_price"], errors="coerce")
        assets = pd.to_numeric(group["total_assets"], errors="coerce")
        finite_share_mask = np.isfinite(share_prices.to_numpy(dtype=float, na_value=np.nan))
        finite_share_times = group.index[finite_share_mask]
        has_history = bool(finite_share_times.size) and finite_share_times.max() - finite_share_times.min() >= MIN_SPARKLINE_HISTORY
        if has_history and np.isfinite(assets.to_numpy(dtype=float, na_value=np.nan)).any():
            included.add(str(vault_id))
    return included


def prepare_vault_sparklines(prices_df: pd.DataFrame, included_ids: set[str]) -> tuple[list[tuple[str, SparklineData]], int]:
    """Prepare daily sparse observations for all eligible vaults.

    :param prices_df:
        Naive-UTC price rows with ``id``, ``share_price`` and ``total_assets``.
    :param included_ids:
        IDs admitted by :func:`get_included_vault_ids`.
    :return:
        Prepared chart data and the number of IDs skipped for insufficient
        finite history.
    """
    selected = prices_df.loc[prices_df["id"].astype(str).isin(included_ids), ["id", "share_price", "total_assets"]].copy()
    selected.index.name = "timestamp"
    selected = selected.reset_index().set_index(["id", "timestamp"]).sort_index()
    selected_ids = set(selected.index.get_level_values("id"))
    prepared: list[tuple[str, SparklineData]] = []
    skipped = 0
    for vault_id in sorted(included_ids):
        if vault_id not in selected_ids:
            skipped += 1
            continue
        sparkline_data = prepare_sparkline_data(selected.loc[vault_id])
        if sparkline_data is None:
            skipped += 1
            logger.debug("Skipping sparkline for vault %s: less than %s of finite history", vault_id, MIN_SPARKLINE_HISTORY)
        else:
            prepared.append((vault_id, sparkline_data))
    return prepared, skipped


def latest_total_assets_by_id(prices_df: pd.DataFrame, prepared: list[tuple[str, SparklineData]]) -> dict[str, float | None]:
    """Resolve the latest finite TVL through each chart's final UTC day.

    :param prices_df:
        Price rows indexed by naive UTC timestamps with ``id`` and
        ``total_assets`` columns.
    :param prepared:
        Prepared charts whose end dates bound the TVL lookup.
    :return:
        Mapping from vault ID to the latest finite native-unit TVL, or ``None``
        when no usable value exists in the chart's final day.
    """
    grouped = prices_df.assign(id=prices_df["id"].astype(str)).groupby("id", sort=False)["total_assets"]
    result: dict[str, float | None] = {}
    for vault_id, sparkline_data in prepared:
        try:
            rows = grouped.get_group(vault_id)
        except KeyError:
            result[vault_id] = None
            continue
        chart_end = sparkline_data.end_at + pd.Timedelta(days=1)
        numeric = pd.to_numeric(rows.loc[rows.index < chart_end], errors="coerce")
        finite_mask = np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan))
        finite = numeric.iloc[finite_mask].sort_index()
        result[vault_id] = float(finite.iloc[-1]) if not finite.empty else None
    return result


def calculate_sparkline_input_digest(sparkline_data: SparklineData) -> str:
    """Calculate a canonical renderer/input digest before rendering."""
    hasher = hashlib.sha256()
    hasher.update(b"eth-defi-sparkline-input-v1")
    hasher.update(struct.pack("<I", SPARKLINE_RENDERER_VERSION))
    hasher.update(struct.pack("<II", SPARKLINE_SVG_WIDTH, SPARKLINE_SVG_HEIGHT))
    hasher.update(struct.pack("<II", SPARKLINE_PNG_WIDTH, SPARKLINE_PNG_HEIGHT))
    for style in (SPARKLINE_LINE_COLOR, SPARKLINE_GRADIENT_TOP_COLOR, SPARKLINE_BACKGROUND_COLOR):
        encoded = style.encode("ascii")
        hasher.update(struct.pack("<I", len(encoded)))
        hasher.update(encoded)
    hasher.update(struct.pack("<d", SPARKLINE_GRADIENT_ALPHA))
    hasher.update(struct.pack("<II", SPARKLINE_SVG_LINE_WIDTH, SPARKLINE_LINE_WIDTH))
    hasher.update(struct.pack("<II", SPARKLINE_SVG_MARGIN_RATIO, SPARKLINE_PNG_MARGIN_RATIO))
    hasher.update(struct.pack("<q", sparkline_data.start_at.value))
    hasher.update(struct.pack("<q", sparkline_data.end_at.value))
    ordered = sparkline_data.prices_df.sort_index()
    for timestamp, value in zip(ordered.index, ordered["share_price"].to_numpy(dtype=float), strict=True):
        hasher.update(struct.pack("<q", int(timestamp.value)))
        hasher.update(struct.pack("<d", float(value)))
    return hasher.hexdigest()


def render_vault_sparklines(vault_id: str, sparkline_data: SparklineData) -> list[RenderData]:
    """Render one vault's deterministic SVG and PNG payloads.

    :param vault_id:
        Canonical chain-address vault identifier used in object keys.
    :param sparkline_data:
        Prepared daily observations and fixed chart bounds.
    :return:
        Exactly one SVG and one PNG payload.
    :raises ValueError:
        If the prepared data cannot be rendered.
    """
    return [
        {
            "vault_id": vault_id,
            "payload": render_sparkline_svg(
                sparkline_data,
                width=SPARKLINE_SVG_WIDTH,
                height=SPARKLINE_SVG_HEIGHT,
                line_width=SPARKLINE_SVG_LINE_WIDTH,
                margin_ratio=SPARKLINE_SVG_MARGIN_RATIO,
            ),
            "content_type": "image/svg+xml",
            "extension": "svg",
        },
        {
            "vault_id": vault_id,
            "payload": render_sparkline_png(sparkline_data),
            "content_type": "image/png",
            "extension": "png",
        },
    ]


def render_sparklines(vault_data: list[tuple[str, SparklineData]], max_workers: int) -> list[RenderData]:
    """Render one bounded batch using Joblib threads.

    :param vault_data:
        Vault IDs paired with prepared daily chart data.
    :param max_workers:
        Maximum number of concurrent renderer workers.
    :return:
        Flattened SVG and PNG payloads for successfully rendered vaults.
    """
    if not vault_data:
        return []
    tasks = (delayed(render_vault_sparklines)(vault_id, data) for vault_id, data in vault_data)
    results = Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(vault_data), desc="Rendering sparklines"))
    return [image for images in results for image in images]


def upload_sparkline(s3_client: Any, bucket_name: str, render_data: RenderData) -> bool:
    """Gzip and publish one image, skipping an unchanged R2 object.

    :param s3_client:
        Thread-safe S3-compatible client.
    :param bucket_name:
        Destination R2 bucket.
    :param render_data:
        One deterministic SVG or PNG payload.
    :return:
        ``True`` when an object was uploaded, ``False`` when the remote object
        already matched the source digest.
    """
    payload = render_data["payload"]
    object_name = f"sparkline-90d-{render_data['vault_id']}.{render_data['extension']}"
    return upload_bytes_to_r2(
        s3_client=s3_client,
        payload=gzip.compress(payload, mtime=0),
        bucket_name=bucket_name,
        object_name=object_name,
        content_type=render_data["content_type"],
        content_encoding="gzip",
        skip_if_current=True,
        source_digest=calculate_bytes_digest(payload),
    )


def upload_sparklines(
    s3_client: Any,
    bucket_name: str,
    render_data: list[RenderData],
    max_workers: int,
) -> tuple[int, int]:
    """Upload a bounded image list using the configured thread count.

    This compatibility helper retains the standalone script's previous public
    API. The production coordinator uploads per-vault pairs directly so one
    partial pair cannot be mistaken for a complete publication.

    :param s3_client:
        Authenticated S3-compatible client.
    :param bucket_name:
        Destination R2 bucket.
    :param render_data:
        Rendered image payloads.
    :param max_workers:
        Upload thread count.
    :return:
        Number of uploaded and unchanged objects.
    """
    if not render_data:
        return 0, 0
    tasks = (delayed(upload_sparkline)(s3_client, bucket_name, image) for image in render_data)
    results = Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(render_data), desc=f"Uploading sparklines to R2 bucket {bucket_name}"))
    uploaded = sum(results)
    return uploaded, len(results) - uploaded


def _classify_latest_tvl(symbol: str | None, latest_total_assets: float | None) -> tuple[DenominationFamily, Decimal, bool] | None:
    """Classify latest TVL, returning None for invalid source data."""
    if latest_total_assets is None or not math.isfinite(latest_total_assets) or latest_total_assets < 0:
        return None
    family = classify_denomination(symbol)
    if family not in SUPPORTED_SPARKLINE_FAMILIES:
        return None
    threshold = resolve_sparkline_tvl_threshold(symbol)
    latest_decimal = Decimal(str(latest_total_assets))
    return family, threshold, latest_decimal < threshold


def _is_due(entry: dict[str, Any] | None, *, low_tvl: bool, now: datetime.datetime, force: bool) -> bool:
    """Apply retry precedence and the 72-hour low-TVL cadence."""
    if force:
        return True
    if entry is not None:
        next_retry_at = _parse_timestamp(entry.get("next_retry_at"))
        if next_retry_at is not None and next_retry_at > now:
            return False
    if not low_tvl:
        return True
    if entry is None:
        return True
    last_completed_at = _parse_timestamp(entry.get("last_completed_at"))
    return last_completed_at is None or now >= last_completed_at + SPARKLINE_LOW_TVL_INTERVAL


def _has_current_success(
    entry: dict[str, Any],
    digest: str,
    publication_target: str,
    *,
    renderer_state_is_current: bool,
) -> bool:
    """Return whether a prior successful publication can skip rendering."""
    return renderer_state_is_current and entry.get("renderer_version") == SPARKLINE_RENDERER_VERSION and entry.get("publication_target") == publication_target and entry.get("input_sha256") == digest and bool(entry.get("last_completed_at"))


def _failure_backoff(now: datetime.datetime, consecutive_failures: int) -> str:
    """Return the next persisted retry timestamp after one final attempt."""
    delay_hours = min(24, 6 * (2 ** max(0, consecutive_failures - 1)))
    return _format_timestamp(now + datetime.timedelta(hours=delay_hours))


def _record_failure(entry: dict[str, Any], now: datetime.datetime) -> None:
    """Advance failure state while preserving the last successful digest."""
    failures = int(entry.get("consecutive_failures", 0)) + 1
    entry["consecutive_failures"] = failures
    entry["next_retry_at"] = _failure_backoff(now, failures)


def load_sparkline_price_data(prices_path: Path) -> pd.DataFrame:
    """Load and validate the canonical daily crypto sparkline Parquet.

    :param prices_path:
        Daily crypto-vault Parquet path.
    :return:
        Stable-sorted price rows with numeric ``share_price`` and
        ``total_assets`` columns.
    :raises ValueError:
        If the file schema or timestamp index is incompatible with export.
    """
    prices_df = pd.read_parquet(prices_path)
    required_columns = {"id", "share_price", "total_assets"}
    missing = required_columns - set(prices_df.columns)
    if missing:
        raise ValueError(f"Sparkline Parquet is missing columns: {sorted(missing)!r}")
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        message = "Sparkline Parquet must have a naive UTC timestamp index"
        raise ValueError(message)
    if prices_df.index.tz is not None:
        message = "Sparkline Parquet timestamp index must be naive UTC"
        raise ValueError(message)
    prices_df.index.name = "timestamp"
    prices_df["id"] = prices_df["id"].astype(str)
    prices_df["share_price"] = pd.to_numeric(prices_df["share_price"], errors="coerce")
    prices_df["total_assets"] = pd.to_numeric(prices_df["total_assets"], errors="coerce")
    if not np.isfinite(prices_df["share_price"].to_numpy(dtype=float, na_value=np.nan)).any():
        message = "Sparkline Parquet has no numeric share prices"
        raise ValueError(message)
    if not np.isfinite(prices_df["total_assets"].to_numpy(dtype=float, na_value=np.nan)).any():
        message = "Sparkline Parquet has no numeric total_assets values"
        raise ValueError(message)
    return prices_df.sort_index(kind="stable")


def _create_s3_client_from_environment(max_workers: int) -> tuple[Any, str]:
    """Create the configured sparkline R2 client and resolve its bucket."""
    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    if not bucket_name or not endpoint_url or not access_key_id or not secret_access_key:
        message = "R2 sparkline credentials and endpoint environment variables are required"
        raise ValueError(message)
    return (
        create_r2_client(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            max_pool_connections=max_workers,
        ),
        bucket_name,
    )


def _render_one_safe(vault_id: str, sparkline_data: SparklineData) -> tuple[str, list[RenderData], str | None]:
    """Render one vault while keeping failures isolated to that vault."""
    try:
        images = render_vault_sparklines(vault_id, sparkline_data)
        if len(images) != SPARKLINE_FORMAT_COUNT or {image["extension"] for image in images} != {"svg", "png"}:
            return vault_id, [], "Renderer did not produce both SVG and PNG outputs"
        return vault_id, images, None
    except Exception as exc:
        return vault_id, [], str(exc)


def _publish_rendered_vault(
    s3_client: Any,
    bucket_name: str,
    images: list[RenderData],
) -> tuple[int, int, str | None]:
    """Publish one vault's complete image pair in one worker task.

    The image pair remains the publication unit: a failure after one successful
    object preserves the partial upload counters but returns an error so the
    caller does not advance the vault's completion digest.

    :param s3_client:
        Shared thread-safe S3-compatible client.
    :param bucket_name:
        Destination R2 bucket.
    :param images:
        Exactly one SVG and one PNG render for a vault.
    :return:
        Uploaded count, unchanged count and an optional terminal error string.
    """
    uploaded = 0
    unchanged = 0
    try:
        for image in images:
            if upload_sparkline(s3_client, bucket_name, image):
                uploaded += 1
            else:
                unchanged += 1
    except Exception as exc:
        return uploaded, unchanged, str(exc)
    return uploaded, unchanged, None


def run_sparkline_export(  # noqa: PLR0914
    *,
    data_dir: Path | None = None,
    vault_db_path: Path | None = None,
    prices_path: Path | None = None,
    state_path: Path | None = None,
    max_workers: int | None = None,
    batch_size: int | None = None,
    force: bool | None = None,
) -> SparklineExportResult:
    """Run bounded deterministic sparkline rendering and publication.

    :param data_dir:
        Pipeline data directory used to resolve omitted paths.
    :param vault_db_path:
        Vault metadata database path.
    :param prices_path:
        Canonical crypto daily price Parquet path.
    :param state_path:
        Persistent sparkline state path.
    :param max_workers:
        Thread count for bounded rendering and upload work.
    :param batch_size:
        Maximum number of vaults whose image payloads coexist in memory. When
        omitted, ``SPARKLINE_BATCH_SIZE`` is read from the environment.
    :param force:
        Bypass cadence, retry backoff and local digest skips.
    :return:
        Run counters and terminal status.
    """
    data_dir = data_dir or get_pipeline_data_dir()
    vault_db_path = vault_db_path or data_dir / "vault-metadata-db.pickle"
    prices_path = prices_path or data_dir / "crypto-vaults" / "crypto-cleaned-vault-prices-1d.parquet"
    state_path = state_path or data_dir / "sparkline-export-state.json"
    max_workers = int(os.environ.get("SPARKLINE_MAX_WORKERS", "8")) if max_workers is None else max_workers
    batch_size = int(os.environ.get("SPARKLINE_BATCH_SIZE", str(SPARKLINE_BATCH_SIZE))) if batch_size is None else batch_size
    force = force if force is not None else os.environ.get("FORCE_SPARKLINE_EXPORT", "false").lower() == "true"
    if max_workers < 1 or batch_size < 1:
        message = "Sparkline workers and batch size must be positive"
        raise ValueError(message)

    now = native_datetime_utc_now()
    prices_df = load_sparkline_price_data(prices_path)
    vault_db = VaultDatabase.read(vault_db_path)
    included_ids = get_included_vault_ids(vault_db, prices_df)
    prepared, skipped = prepare_vault_sparklines(prices_df, included_ids)
    latest_assets = latest_total_assets_by_id(prices_df, prepared)
    symbols = _vault_symbol_map(vault_db)
    state = load_sparkline_state(state_path, now)
    renderer_state_is_current = state.get("renderer_version") == SPARKLINE_RENDERER_VERSION
    s3_client, bucket_name = _create_s3_client_from_environment(max_workers)
    publication_target = _publication_target(bucket_name)
    counters = {
        "eligible": len(included_ids),
        "insufficient_history": skipped,
        "invalid_tvl": 0,
        "low_tvl_throttled": 0,
        "retry_deferred": 0,
        "unchanged": 0,
        "rendered": 0,
        "uploaded": 0,
        "r2_unchanged": 0,
        "failed": 0,
        "batches": 0,
    }

    for offset in tqdm(range(0, len(prepared), batch_size), desc="Exporting sparklines"):
        batch = prepared[offset : offset + batch_size]
        due: list[tuple[str, SparklineData, str]] = []
        for vault_id, sparkline_data in batch:
            entry = state["vaults"].get(vault_id)
            classification = _classify_latest_tvl(symbols.get(vault_id), latest_assets.get(vault_id))
            if classification is None:
                counters["invalid_tvl"] += 1
                if entry is not None:
                    entry["last_seen_at"] = _format_timestamp(now)
                logger.warning("Skipping sparkline for vault %s because latest TVL is invalid", vault_id)
                continue
            family, threshold, low_tvl = classification
            if entry is None:
                entry = {"consecutive_failures": 0, "next_retry_at": None}
                state["vaults"][vault_id] = entry
            entry.update(
                {
                    "denomination_family": family.value,
                    "low_tvl": low_tvl,
                    "latest_total_assets": latest_assets[vault_id],
                    "threshold": float(threshold),
                    "last_seen_at": _format_timestamp(now),
                }
            )
            entry_renderer_is_current = entry.get("renderer_version") == SPARKLINE_RENDERER_VERSION
            entry_target_is_current = entry.get("publication_target") == publication_target
            publication_is_invalidated = not renderer_state_is_current or not entry_renderer_is_current or not entry_target_is_current
            if not _is_due(
                entry,
                # A renderer or destination change invalidates the low-TVL
                # cadence, but a failed attempt must still honour its backoff.
                low_tvl=low_tvl and not publication_is_invalidated,
                now=now,
                force=force,
            ):
                next_retry_at = _parse_timestamp(entry.get("next_retry_at"))
                if next_retry_at is not None and next_retry_at > now:
                    counters["retry_deferred"] += 1
                else:
                    counters["low_tvl_throttled"] += 1
                continue
            digest = calculate_sparkline_input_digest(sparkline_data)
            if not force and _has_current_success(entry, digest, publication_target, renderer_state_is_current=renderer_state_is_current):
                entry.update(
                    {
                        "last_attempted_at": _format_timestamp(now),
                        "last_completed_at": _format_timestamp(now),
                        "consecutive_failures": 0,
                        "next_retry_at": None,
                    }
                )
                counters["unchanged"] += 1
                continue
            due.append((vault_id, sparkline_data, digest))

        if due:
            tasks = (delayed(_render_one_safe)(vault_id, sparkline_data) for vault_id, sparkline_data, _ in due)
            rendered_results = Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(due), desc="Rendering sparkline batch"))
            digest_by_id = {vault_id: digest for vault_id, _, digest in due}
            successful_rendered_results = [result for result in rendered_results if result[2] is None]
            publication_tasks = (delayed(_publish_rendered_vault)(s3_client, bucket_name, images) for _, images, _ in successful_rendered_results)
            publication_values = Parallel(n_jobs=max_workers, prefer="threads")(tqdm(publication_tasks, total=len(successful_rendered_results), desc="Uploading sparkline batch"))
            publication_results = {vault_id: publication_result for (vault_id, _, _), publication_result in zip(successful_rendered_results, publication_values, strict=True)}
            for vault_id, _images, render_error in rendered_results:
                entry = state["vaults"][vault_id]
                entry["last_attempted_at"] = _format_timestamp(now)
                if render_error is not None:
                    _record_failure(entry, now)
                    counters["failed"] += 1
                    logger.warning("Sparkline render failed for vault %s; retry after %s: %s", vault_id, entry["next_retry_at"], render_error)
                    continue
                counters["rendered"] += 1
                uploaded_count, unchanged_count, publication_error = publication_results[vault_id]
                counters["uploaded"] += uploaded_count
                counters["r2_unchanged"] += unchanged_count
                if publication_error is not None:
                    _record_failure(entry, now)
                    counters["failed"] += 1
                    logger.warning("Sparkline publication failed for vault %s; retry after %s: %s", vault_id, entry["next_retry_at"], publication_error)
                    continue
                entry.update(
                    {
                        "input_sha256": digest_by_id[vault_id],
                        "last_completed_at": _format_timestamp(now),
                        "renderer_version": SPARKLINE_RENDERER_VERSION,
                        "publication_target": publication_target,
                        "consecutive_failures": 0,
                        "next_retry_at": None,
                    }
                )
                if uploaded_count:
                    entry["last_uploaded_at"] = _format_timestamp(now)
        counters["batches"] += 1
        save_sparkline_state(state, state_path, now)
        if due:
            del rendered_results, successful_rendered_results, publication_results

    if not prepared:
        # Give retention a chance to prune state even when this input snapshot
        # contains no vault with enough history to form a render batch.
        save_sparkline_state(state, state_path, now)

    logger.info(
        "Sparkline export complete: eligible=%d insufficient_history=%d invalid_tvl=%d throttled=%d retry_deferred=%d unchanged=%d rendered=%d uploaded=%d r2_unchanged=%d failed=%d",
        counters["eligible"],
        counters["insufficient_history"],
        counters["invalid_tvl"],
        counters["low_tvl_throttled"],
        counters["retry_deferred"],
        counters["unchanged"],
        counters["rendered"],
        counters["uploaded"],
        counters["r2_unchanged"],
        counters["failed"],
    )
    return SparklineExportResult(success=counters["failed"] == 0, counters=counters)
