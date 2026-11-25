"""Post-processing pipeline for vault price data.

Merges native protocol data (Hypercore, GRVT, Lighter, Hibachi, ApeX, Derive v3) into the
uncleaned parquet, runs the cleaning pipeline, and optionally
uploads results to R2.

Used by both :py:mod:`scan-vaults-all-chains` and
:py:mod:`post-process-prices` scripts.
"""

import collections
import datetime
import importlib.util
import json
import logging
import os
import pickle  # noqa: S403 - VaultDatabase already uses trusted local pickle state.
import time
from functools import cache
from pathlib import Path
from typing import Any, Callable

try:
    import brotli
except ImportError:
    brotli = None

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from eth_defi.apex.constants import APEX_CHAIN_ID, APEX_METRICS_DATABASE
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.apex.vault_data_export import build_raw_prices_dataframe as build_apex_prices_dataframe
from eth_defi.cloudflare_r2 import R2OperationError, R2RetryableOperationError, R2SourceDigest, calculate_bytes_digest, copy_r2_object_daily_backup, create_r2_client, upload_bytes_to_r2, upload_file_to_r2
from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.parquet import materialise_exchange_rate_parquet
from eth_defi.derive.v3_constants import DERIVE_V3_CHAIN_ID, DERIVE_V3_MAINNET_DATABASE
from eth_defi.derive.v3_vault_data_export import build_raw_prices_dataframe as build_derive_v3_prices_dataframe
from eth_defi.derive.v3_vault_metrics import DeriveV3VaultDatabase
from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault_data_export import build_raw_prices_dataframe as build_grvt_prices_dataframe
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID, HIBACHI_DAILY_METRICS_DATABASE
from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
from eth_defi.hibachi.vault_data_export import build_raw_prices_dataframe as build_hibachi_prices_dataframe
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE, HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.high_freq_metrics import HyperliquidHighFreqMetricsDatabase
from eth_defi.hyperliquid.permission import PERMISSION_FILENAME
from eth_defi.hyperliquid.vault_data_export import build_hypercore_prices_dataframe, export_hypercore_permission_history, merge_into_vault_database
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DAILY_METRICS_DATABASE, LIGHTER_DEPLOYMENTS, LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID, LIGHTER_ROBINHOOD
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.vault_data_export import build_raw_prices_dataframe as build_lighter_prices_dataframe
from eth_defi.lighter.vault_data_export import get_lighter_price_deployments
from eth_defi.neutral_trade.constants import NEUTRAL_TRADE_DATABASE, SOLANA_DATASET_CHAIN_ID
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase
from eth_defi.neutral_trade.vault_data_export import build_raw_prices_dataframe as build_neutral_trade_prices
from eth_defi.perp_dex.adapter import PerpDexCapability, PerpDexCapabilityRegistry, embed_perp_capability_registry
from eth_defi.perp_dex.parquet import attach_perp_metrics_to_price_rows, derive_perp_vault_metric_snapshots
from eth_defi.perp_dex.storage import read_perp_vault_observations
from eth_defi.research.sparkline_export import run_sparkline_export
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault import top_vaults_json
from eth_defi.vault.base import MAX_VAULT_PRICE_ROW_AGE, MIN_MEANINGFUL_TVL_EXIT_USD, MIN_MEANINGFUL_TVL_USD, VaultHistoricalRead, VaultSpec
from eth_defi.vault.crypto_vault_export import publish_crypto_vault_bundle
from eth_defi.vault.crypto_vaults import CryptoVaultPaths, build_crypto_vault_metadata, build_crypto_vault_prices, resolve_crypto_vault_paths
from eth_defi.vault.data_file_export import (
    publish_exchange_rate_parquet_to_alternative_bucket,
    resolve_exchange_rate_database_path,
    resolve_exchange_rate_parquet_path,
)
from eth_defi.vault.incremental_cleaning import changed_histories, cleaning_inputs, cleaning_snapshot, replace_changed_histories
from eth_defi.vault.publication_state import NATIVE_REVISIONS_KEY, duckdb_revision, file_revision, read_publication_state, write_publication_state
from eth_defi.vault.sample_export import export_sample_files_to_r2
from eth_defi.vault.scan_manifest import publish_hypercore_permission_manifest, publish_vault_scan_manifest
from eth_defi.vault.scan_timing import ScanTimings
from eth_defi.vault.settlement_data import get_default_vault_settlement_database_path
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, VaultDatabase, get_pipeline_data_dir

#: Required env vars for the top-vaults JSON R2 upload.
#: See :py:func:`validate_top_vaults_config`.
_R2_TOP_VAULTS_REQUIRED_ENV_VARS = (
    "R2_TOP_VAULTS_BUCKET_NAME",
    "R2_TOP_VAULTS_ACCESS_KEY_ID",
    "R2_TOP_VAULTS_SECRET_ACCESS_KEY",
    "R2_TOP_VAULTS_ENDPOINT_URL",
)

logger = logging.getLogger(__name__)

#: Access-key length below which masking would reveal the entire value.
_MIN_MASKED_ACCESS_KEY_LENGTH = 8


PERP_DEX_CAPABILITY_REGISTRY = PerpDexCapabilityRegistry(
    capabilities=(
        PerpDexCapability("hyperliquid", "hypercore", "USDC", True, "available", 14_400, 300),
        PerpDexCapability("lighter", "ethereum", "USDC", True, "available", 14_400, 300),
        PerpDexCapability("lighter", "robinhood", "USDG", True, "available", 14_400, 300),
        PerpDexCapability("grvt", "mainnet", "USDT", False, "authentication_required", 86_400, 0),
        PerpDexCapability("hibachi", "mainnet", "USDT", False, "not_public", 86_400, 0),
        PerpDexCapability("apex", "omni", "USDT", False, "authentication_required", 14_400, 0),
        PerpDexCapability("derive", "mainnet", "USD", False, "authentication_required", 86_400, 0),
    )
)


def _append_perp_metric_snapshots(database: object, target: list[pd.DataFrame], deployment: str | None = None) -> None:
    """Append shared observation snapshots when a native database supports them.

    Lightweight test doubles and pre-migration third-party database wrappers
    may not expose a DuckDB connection.  Their price merge must retain its
    previous behaviour. A live connection must have the expected observation schema.

    :param database:
        Native metrics database object.
    :param target:
        Mutable snapshot list used by the current native merge.
    :param deployment: Restrict observations to one native deployment.
    :return:
        ``None``.
    """
    connection = getattr(database, "con", None)
    if connection is None:
        return
    read_started_at = time.perf_counter()
    accounts, positions = read_perp_vault_observations(connection, deployment=deployment)
    logger.info("Perp observation read: %d account rows, %d position rows in %.2f seconds", len(accounts), len(positions), time.perf_counter() - read_started_at)
    derive_started_at = time.perf_counter()
    snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
    target.append(snapshots)
    logger.info("Perp correction and exposure derivation: %d account rows -> %d snapshots in %.2f seconds", len(accounts), len(snapshots), time.perf_counter() - derive_started_at)


def _mask_access_key_id(access_key_id: str | None) -> str:
    """Mask an R2 access key ID for safe logging.

    :param access_key_id:
        Raw access key ID.

    :return:
        Masked access key ID.
    """
    if not access_key_id:
        return "<unknown>"
    if len(access_key_id) <= _MIN_MASKED_ACCESS_KEY_LENGTH:
        return access_key_id
    return f"{access_key_id[:4]}...{access_key_id[-4:]}"


def _prepare_top_vaults_brotli_payload(output_path: Path) -> tuple[bytes, R2SourceDigest, int]:
    """Read, hash, and compress one top-vault JSON artefact for bucket reuse.

    The caller caches this result only for the current publication. A later
    export may replace the file at the same path and must prepare new bytes.

    :param output_path: Generated JSON file to publish.
    :return: Compressed payload, digest of the raw JSON, and raw byte count.
    """
    assert brotli is not None
    raw_bytes = output_path.read_bytes()
    source_digest = calculate_bytes_digest(raw_bytes)
    started_at = time.perf_counter()
    compressed = brotli.compress(raw_bytes, quality=11)
    logger.info("Top-vault Brotli compression: %d -> %d bytes in %.2fs", len(raw_bytes), len(compressed), time.perf_counter() - started_at)
    return compressed, source_digest, len(raw_bytes)


def _upload_top_vaults_json_to_bucket(  # noqa: PLR0917 - internal upload payload has six required fields
    s3_client: Any,
    output_path: Path,
    bucket_name: str,
    endpoint_url: str,
    object_key: str,
    access_key_id: str,
    *,
    public_url: str = "",
    bucket_label: str,
    brotli_payload_factory: Callable[[], tuple[bytes, R2SourceDigest, int]] | None = None,
) -> bool:
    """Upload the generated top-vaults JSON to one configured bucket.

    Each bucket is handled independently so that one misconfigured R2
    target does not stop follow-up uploads to other buckets.

    :param s3_client:
        Authenticated boto3 S3 client.

    :param output_path:
        Generated JSON file path.

    :param bucket_name:
        Target bucket name.

    :param endpoint_url:
        R2 endpoint URL for logging.

    :param object_key:
        Destination object key.

    :param access_key_id:
        R2 access key ID for masked logging.

    :param public_url:
        Optional public URL to log for the primary bucket.

    :param bucket_label:
        Human-readable label such as ``primary`` or ``alternative``.

    :param brotli_payload_factory:
        Per-publication cached payload supplier shared by both buckets.

    :return:
        ``True`` if the bucket upload succeeded or was skipped as unchanged,
        otherwise ``False``.
    """
    try:
        uploaded = upload_file_to_r2(
            s3_client=s3_client,
            file_path=output_path,
            bucket_name=bucket_name,
            object_name=object_key,
            skip_if_current=True,
        )
        if uploaded:
            logger.info("Uploaded %s to %s s3://%s/%s", output_path, bucket_label, bucket_name, object_key)
            if public_url:
                logger.info("  -> %s/%s", public_url.rstrip("/"), object_key)
        else:
            logger.info("Skipped unchanged %s for %s s3://%s/%s", output_path, bucket_label, bucket_name, object_key)
    except Exception:
        logger.exception(
            "Top vaults JSON %s bucket upload failed — bucket=%s, endpoint=%s, object_key=%s, access_key_id=%s",
            bucket_label,
            bucket_name,
            endpoint_url,
            object_key,
            _mask_access_key_id(access_key_id),
        )
        return False

    # Upload brotli-compressed variant (.json.br) alongside raw JSON.
    # Brotli is optional; the raw JSON upload remains valid without it.
    if brotli is None:
        logger.warning("brotli package not installed — skipping .json.br upload for %s bucket", bucket_label)
        return False
    try:
        if brotli_payload_factory is None:
            compressed, source_digest, raw_size = _prepare_top_vaults_brotli_payload(output_path)
        else:
            compressed, source_digest, raw_size = brotli_payload_factory()

        upload_started_at = time.perf_counter()
        br_uploaded = upload_bytes_to_r2(
            s3_client=s3_client,
            payload=compressed,
            bucket_name=bucket_name,
            object_name=object_key + ".br",
            content_type="application/json",
            content_encoding="br",
            source_digest=source_digest,
            skip_if_current=True,
        )
        logger.info("Top-vault Brotli %s bucket upload/check: %.2fs", bucket_label, time.perf_counter() - upload_started_at)
        ratio = len(compressed) / raw_size * 100 if raw_size else 0
        if br_uploaded:
            logger.info(
                "Uploaded brotli %s.br to %s s3://%s/%s.br (%.1f%% of original)",
                output_path.name,
                bucket_label,
                bucket_name,
                object_key,
                ratio,
            )
        else:
            logger.info("Skipped unchanged brotli for %s s3://%s/%s.br", bucket_label, bucket_name, object_key)
    except Exception:
        logger.exception("Brotli compression/upload failed for %s bucket — raw JSON already uploaded", bucket_label)
        return False

    return True


def _upload_top_vaults_json_to_configured_buckets(  # noqa: PLR0917 - internal R2 configuration has six required fields
    s3_client: Any,
    output_path: Path,
    bucket_name: str,
    endpoint_url: str,
    object_key: str,
    access_key_id: str,
    *,
    public_url: str = "",
    alt_bucket_name: str | None = None,
) -> bool:
    """Upload the generated top-vaults JSON to all configured buckets.

    The primary and alternative buckets are attempted independently. This
    means a permission issue on one bucket does not stop the other upload or
    later post-processing steps.

    :param s3_client:
        Authenticated boto3 S3 client.
    :param output_path:
        Generated JSON file path.
    :param bucket_name:
        Primary bucket name.
    :param endpoint_url:
        R2 endpoint URL for logging.
    :param object_key:
        Destination object key.
    :param access_key_id:
        R2 access key ID for masked logging.
    :param public_url:
        Optional public URL for the primary bucket.
    :param alt_bucket_name:
        Optional alternative bucket name.
    :return:
        ``True`` if all configured uploads succeeded or were skipped as
        unchanged, otherwise ``False``.
    """

    @cache
    def brotli_payload() -> tuple[bytes, R2SourceDigest, int]:
        """Prepare compression on first use and reuse it for this publication.

        The no-argument cache is local to this call, so a later scanner round
        always reads its newly generated JSON artefact.

        :return: Compressed bytes, source digest and source size.
        """
        return _prepare_top_vaults_brotli_payload(output_path)

    primary_success = _upload_top_vaults_json_to_bucket(
        s3_client=s3_client,
        output_path=output_path,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        object_key=object_key,
        access_key_id=access_key_id,
        public_url=public_url,
        bucket_label="primary",
        brotli_payload_factory=brotli_payload,
    )

    alternative_success = True
    if alt_bucket_name:
        alternative_success = _upload_top_vaults_json_to_bucket(
            s3_client=s3_client,
            output_path=output_path,
            bucket_name=alt_bucket_name,
            endpoint_url=endpoint_url,
            object_key=object_key,
            access_key_id=access_key_id,
            bucket_label="alternative",
            brotli_payload_factory=brotli_payload,
        )

        daily_backup_enabled = os.environ.get("R2_DAILY_BACKUP", "true").lower() != "false"
        if alternative_success and daily_backup_enabled:
            copied = copy_r2_object_daily_backup(s3_client, alt_bucket_name, object_key)
            if copied:
                logger.info("Created daily backup for top_vaults_by_chain.json in alternative bucket")
            else:
                logger.info("Daily backup skipped or failed for top_vaults_by_chain.json in alternative bucket")

    return primary_success and alternative_success


def _create_native_merge_schema(existing_schema: pa.Schema | None, replacements: dict[int, pd.DataFrame]) -> pa.Schema:
    """Construct a canonical schema for the native partition replacement.

    Canonical raw-price columns use the exact types required by the EVM
    scanner. Extra native-protocol fields are retained from the existing
    parquet and unified with fresh source frames, so a native merge cannot
    discard fields such as Hypercore account metrics.

    :param existing_schema:
        Existing raw-price Parquet schema, or ``None`` when creating it.
    :param replacements:
        Fresh native price frames keyed by their synthetic chain IDs.
    :return:
        Canonical schema followed by compatible native-only fields.
    """
    canonical_schema = VaultHistoricalRead.to_pyarrow_schema()
    canonical_names = set(canonical_schema.names)
    extra_schemas: list[pa.Schema] = []

    if existing_schema is not None:
        extra_schemas.append(pa.schema(field for field in existing_schema if field.name not in canonical_names))

    for frame in replacements.values():
        source_schema = pa.Schema.from_pandas(frame, preserve_index=False)
        extra_schemas.append(pa.schema(field for field in source_schema if field.name not in canonical_names))

    extras = pa.unify_schemas(extra_schemas, promote_options="permissive") if extra_schemas else pa.schema([])
    return pa.schema([*canonical_schema, *extras])


def _align_native_merge_table(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Align an Arrow table to the canonical native merge schema.

    Missing columns are null-filled and incompatible source representations
    are cast before concatenation. This mirrors the canonical type guarantees
    of :py:meth:`VaultHistoricalRead.write_uncleaned_parquet` without turning
    the full raw parquet into a pandas DataFrame.

    :param table:
        Existing or fresh native Arrow table.
    :param schema:
        Required output schema.
    :return:
        Table with every required field in schema order.
    """
    arrays: list[pa.ChunkedArray] = []
    for field in schema:
        column_index = table.schema.get_field_index(field.name)
        if column_index == -1:
            arrays.append(pa.chunked_array([pa.nulls(len(table), type=field.type)]))
            continue

        column = table.column(column_index)
        arrays.append(column if column.type == field.type else column.cast(field.type, safe=False))

    return pa.Table.from_arrays(arrays, schema=schema)


def audit_native_price_freshness(
    table: pa.Table,
    horizon: datetime.datetime,
) -> dict[str, str]:
    """Report meaningful native vaults lacking a recent real price observation.

    Native vault writers use USD stablecoin accounting and retain the API's
    source ``timestamp``. A new Parquet ``written_at`` does not refresh a
    stale source row. A vault qualifies at $1,500 and remains qualified down
    to $1,000; a successful live publication expects a real row no older than
    14 days. Missing source observations are reported, never fabricated.

    :param table:
        Combined raw price table containing ``chain``, ``address``,
        ``timestamp``, ``share_price`` and USD ``total_assets`` columns.
    :param horizon:
        Naive UTC publication horizon of this native merge.
    :return:
        Overdue vault IDs mapped to the diagnostic reason.
    """
    native_chain_ids = (HYPERCORE_CHAIN_ID, GRVT_CHAIN_ID, LIGHTER_CHAIN_ID, HIBACHI_CHAIN_ID, APEX_CHAIN_ID)
    mask = pc.is_in(table["chain"], value_set=pa.array(native_chain_ids, type=table["chain"].type))
    native = table.select(["chain", "address", "timestamp", "total_assets", "share_price"]).filter(mask)
    if not len(native):
        return {}
    frame = native.to_pandas().sort_values(["chain", "address", "timestamp"], kind="stable")
    groups = frame.groupby(["chain", "address"], sort=False, dropna=False)
    highest_tvl = groups["total_assets"].max()
    latest = groups.tail(1).set_index(["chain", "address"])
    latest_tvl = pd.to_numeric(latest["total_assets"], errors="coerce")
    qualified = ((highest_tvl >= float(MIN_MEANINGFUL_TVL_USD)) & (latest_tvl >= float(MIN_MEANINGFUL_TVL_EXIT_USD))).fillna(False)
    valid_price = pd.to_numeric(frame["share_price"], errors="coerce").replace([float("inf"), float("-inf")], float("nan")).notna()
    valid_latest = frame.loc[valid_price].groupby(["chain", "address"], sort=False).tail(1).set_index(["chain", "address"])
    latest_timestamp = pd.to_datetime(valid_latest["timestamp"], errors="coerce").reindex(latest.index)
    overdue = qualified & (latest_timestamp.isna() | ((horizon - latest_timestamp) > MAX_VAULT_PRICE_ROW_AGE))
    result = {f"{chain}-{address}": "no_valid_source_observation" if pd.isna(latest_timestamp.loc[(chain, address)]) else "stale_source_timestamp" for chain, address in overdue[overdue].index}
    logger.info("Native price freshness: qualified=%d, overdue=%d", int(qualified.sum()), len(result))
    if result:
        per_chain = collections.Counter(vault_id.split("-", 1)[0] for vault_id in result)
        logger.warning(
            "%d native vaults have no real source price observation within 14 days of %s; per chain id: %s; examples: %s",
            len(result),
            horizon,
            dict(per_chain),
            ", ".join(list(result)[:5]),
        )
    for vault_id, reason in result.items():
        logger.debug("Native vault %s has no real source price observation within 14 days of %s: %s", vault_id, horizon, reason)
    return result


#: Bump when a native builder's output changes for identical source data.
NATIVE_MERGE_VERSION = 1


def _read_native_revisions(path: Path) -> dict[str, Any]:
    """Read the native revision vector bound to the published raw prices.

    :param path: Combined raw Parquet.
    :return: Revisions previously written by the incremental merger.
    """
    if not path.is_file():
        return {}
    metadata = pq.read_schema(path).metadata or {}
    try:
        revisions = json.loads(metadata.get(NATIVE_REVISIONS_KEY, b"{}"))
    except ValueError:
        return {}
    return revisions if isinstance(revisions, dict) else {}


def _lighter_revisions(path: Path) -> dict[str, Any]:
    """Fingerprint Lighter's independently replaceable source deployments.

    The stores share a file, so the file generation alone would unnecessarily
    invalidate the other deployment. The flow builder also treats a day's
    counters as provisional until that UTC day has ended.

    :param path: Lighter DuckDB source.
    :return: Per-deployment revisions or an empty mapping for a legacy schema.
    """
    if not path.is_file():
        return {}
    scopes = {deployment.slug: [] for deployment in LIGHTER_DEPLOYMENTS}
    today = native_datetime_utc_now().date()
    with duckdb.connect(str(path), read_only=True) as connection:
        for table in ("pool_daily_prices", "pool_metadata", "perp_vault_account_observations"):
            expected_column = "deployment_slug" if table == "perp_vault_account_observations" else "deployment"
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()}
            if expected_column not in columns:
                return {}
            date_projection = ", max(date)" if table == "pool_daily_prices" else ", NULL"
            rows = connection.execute(
                f"SELECT {expected_column}, count(*), sum(hash(t)::HUGEINT), bit_xor(hash(t)) {date_projection} FROM {table} t GROUP BY {expected_column}"  # noqa: S608
            ).fetchall()
            by_scope = {slug: [count, total, xor, min(last_date + datetime.timedelta(days=1), today).isoformat() if last_date else None] for slug, count, total, xor, last_date in rows}
            for slug, revision in scopes.items():
                revision.append(by_scope.get(slug))
        position_rows = connection.execute(
            """
            SELECT a.deployment_slug, count(*), sum(hash(p)::HUGEINT), bit_xor(hash(p))
            FROM perp_vault_position_observations p
            JOIN perp_vault_account_observations a USING (snapshot_id)
            GROUP BY a.deployment_slug
            """
        ).fetchall()
        positions_by_scope = {slug: [count, total, xor] for slug, count, total, xor in position_rows}
        for slug, revision in scopes.items():
            revision.append(positions_by_scope.get(slug))

    return scopes


def _native_address_keys(chain: pa.ChunkedArray, addresses: pa.ChunkedArray) -> pa.ChunkedArray:
    """Canonicalise merge keys without modifying retained Parquet values.

    :param chain: Raw chain IDs.
    :param addresses: Raw vault addresses, including legacy aliases.
    :return: Case-normalised keys that preserve case-sensitive Solana accounts.
    """
    keys = pc.utf8_lower(addresses)
    solana = pc.equal(chain, SOLANA_DATASET_CHAIN_ID)
    if pc.any(solana).as_py():
        unique = pc.unique(addresses.filter(solana))
        canonical = pa.array([VaultSpec(SOLANA_DATASET_CHAIN_ID, address).vault_address if address is not None else None for address in unique.to_pylist()], type=keys.type)
        if not canonical.equals(pc.utf8_lower(unique)):
            indices = pc.index_in(addresses, value_set=unique)
            keys = pc.if_else(solana, canonical.take(indices), keys)
    return keys


def _assert_native_keys(table: pa.Table) -> None:
    """Reject null or repeated logical keys in the sorted replacement rows.

    :param table: Replacement keys ordered by chain, canonical address and time.
    """
    columns = [table["chain"], table["address"], table["timestamp"]]
    if any(column.null_count for column in columns):
        raise ValueError("Native merge produced a null price identity")
    if len(table) > 1:
        same_chain, same_address, same_timestamp = (pc.equal(column.slice(0, len(table) - 1), column.slice(1)) for column in columns)
        if pc.any(pc.and_(pc.and_(same_chain, same_address), same_timestamp)).as_py():
            raise ValueError("Native merge produced duplicate canonical price keys")


def _assert_native_coverage(previous: pa.Table, replacement: pa.Table) -> None:
    """Reject a shortened source archive before it replaces published history.

    Internal point corrections can change the set of timestamps. A lost vault
    or a contracted time range calls for a reviewed repair with explicit
    ownership, especially when a restored DuckDB is only partly populated.

    :param previous: Keys from the owned scope, excluding explicit retirements.
    :param replacement: Keys supplied for those scopes by the source builders.
    """
    if not previous.num_rows:
        return
    coverage = []
    for table in (previous, replacement):
        table = table.select(["chain", "address", "timestamp"])
        table = table.set_column(1, "address", _native_address_keys(table["chain"], table["address"]))
        bounds = table.group_by(["chain", "address"]).aggregate([("timestamp", "min"), ("timestamp", "max")])
        coverage.append({(row["chain"], row["address"]): (row["timestamp_min"], row["timestamp_max"]) for row in bounds.to_pylist()})
    existing, incoming = coverage
    for key, (first, last) in existing.items():
        bounds = incoming.get(key)
        if bounds is None or bounds[0] is None or bounds[1] is None or (first is not None and bounds[0] > first) or (last is not None and bounds[1] < last):
            message = f"Native replacement would shrink published history for {key}. Restore the complete source or apply a verified repair."
            raise ValueError(message)


def _write_native_partitions_to_uncleaned_parquet(
    parquet_path: Path,
    replacements: dict[int, pd.DataFrame],
    remove_chain_ids: set[int] | None = None,
    replacement_address_patterns: dict[int, set[str]] | None = None,
    capability_registry: PerpDexCapabilityRegistry | None = None,
    revisions: dict[str, Any] | None = None,
) -> int:
    """Replace native chain partitions using PyArrow without full pandas conversion.

    The existing file is read as an Arrow table, only the successful native
    chains are removed, and fresh source frames are converted and aligned once.
    The combined table is sorted for compression efficiency, verified in a
    sibling temporary file, and atomically swapped into place.

    :param parquet_path:
        Raw vault-price parquet to update.
    :param replacements:
        Fresh, non-empty source frames keyed by their synthetic chain IDs.
    :param remove_chain_ids:
        Additional obsolete chain partitions to remove without replacing.
        Currently used to clean the legacy Lighter Robinhood synthetic chain
        9996 once Robinhood data is available under the shared chain 9998.
    :param replacement_address_patterns:
        Optional regular-expression address namespaces for partial replacement
        within a shared synthetic chain. Currently used by Lighter so an
        independently successful Ethereum or Robinhood scan replaces only its
        own addresses within chain 9998.
    :param capability_registry: Perpetual futures metric definitions to embed in this output.
    :param revisions: Native source revisions committed with this output.
    :return:
        Total row count in the new raw parquet.
    """
    assert replacements, "At least one native chain replacement is required"
    replacement_address_patterns = replacement_address_patterns or {}
    assert set(replacement_address_patterns).issubset(replacements), "Address-scoped replacement requires a fresh frame for the same chain"

    existing_table = pq.read_table(parquet_path) if parquet_path.exists() else None
    schema = _create_native_merge_schema(existing_table.schema if existing_table is not None else None, replacements)
    ownership_patterns = {chain_id: "|".join(f"(?:{pattern})" for pattern in sorted(patterns)) for chain_id, patterns in replacement_address_patterns.items()}
    replacement_tables = []
    for chain_id, frame in replacements.items():
        table = _align_native_merge_table(pa.Table.from_pandas(frame, preserve_index=False), schema)
        if revisions is not None:
            owned = pc.equal(table["chain"], chain_id)
            if pattern := ownership_patterns.get(chain_id):
                address_in_scope = pc.match_substring_regex(pc.utf8_lower(table["address"]), pattern=pattern)
                owned = pc.and_(owned, address_in_scope)
            if not table.num_rows or pc.any(pc.invert(pc.fill_null(owned, False))).as_py():
                raise ValueError(f"Native replacement contains rows outside its scope on chain {chain_id}")
        replacement_tables.append(table)
    native_table = pa.concat_tables(replacement_tables)

    retained_rows = 0
    if existing_table is not None:
        # Most native protocols own a whole synthetic chain partition. Lighter
        # is different: Ethereum and Robinhood now share chain 9998 and scan
        # independently, so its replacement mask is narrowed by synthetic
        # address namespace below.
        whole_chain_ids = (set(replacements) - set(replacement_address_patterns)) | (remove_chain_ids or set())
        chain_mask = pc.is_in(existing_table["chain"], value_set=pa.array(sorted(whole_chain_ids), type=existing_table["chain"].type))
        for chain_id, pattern in ownership_patterns.items():
            address_mask = pc.match_substring_regex(pc.utf8_lower(existing_table["address"]), pattern=pattern)
            scoped_chain_mask = pc.and_(
                pc.equal(existing_table["chain"], pa.scalar(chain_id, type=existing_table["chain"].type)),
                address_mask,
            )
            chain_mask = pc.or_(chain_mask, scoped_chain_mask)

        retained_table = _align_native_merge_table(existing_table.filter(pc.invert(chain_mask)), schema)
        removed_rows = pc.sum(chain_mask).as_py() or 0
        retained_rows = retained_table.num_rows
        if retained_rows + removed_rows != existing_table.num_rows:
            raise ValueError("Native merge did not account for all committed raw rows")
        if revisions is not None and removed_rows:
            replacements_only = pc.and_(chain_mask, pc.is_in(existing_table["chain"], value_set=pa.array(sorted(replacements), type=existing_table["chain"].type)))
            _assert_native_coverage(existing_table.select(["chain", "address", "timestamp"]).filter(replacements_only), native_table)
        combined_table = pa.concat_tables([retained_table, native_table])
        logger.info("Native merge row accounting: %d removed, %d retained, %d replacement rows", removed_rows, retained_table.num_rows, native_table.num_rows)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined_table = native_table

    keys = combined_table.select(["chain", "address", "timestamp"])
    keys = keys.set_column(1, "address", _native_address_keys(keys["chain"], keys["address"]))
    sort_indices = pc.sort_indices(keys, sort_keys=[("chain", "ascending"), ("address", "ascending"), ("timestamp", "ascending")])
    combined_table = combined_table.take(sort_indices)

    if capability_registry is not None:
        combined_table = combined_table.replace_schema_metadata(embed_perp_capability_registry(combined_table.schema, capability_registry).metadata)
    elif existing_table is not None:
        # Standalone protocol merges must retain the previously published
        # capability contract when they have no replacement registry.
        metadata = {key: value for key, value in (existing_table.schema.metadata or {}).items() if key.startswith(b"perp_dex.")}
        combined_table = combined_table.replace_schema_metadata(metadata or None)

    if revisions is not None:
        # The sort already orders the owned rows. EVM rows can legitimately
        # share a vault and timestamp when they were read at different blocks.
        replacement_indices = pc.filter(sort_indices, pc.greater_equal(sort_indices, retained_rows)) if retained_rows else sort_indices
        _assert_native_keys(keys.take(replacement_indices))
        metadata = (combined_table.schema.metadata or {}) | {NATIVE_REVISIONS_KEY: json.dumps(revisions, sort_keys=True).encode()}
        combined_table = combined_table.replace_schema_metadata(metadata)

    if "total_assets" in combined_table.column_names:
        audit_native_price_freshness(combined_table, native_datetime_utc_now())

    write_started_at = time.perf_counter()
    VaultHistoricalRead.write_uncleaned_arrow_table(combined_table, parquet_path)
    logger.info("Native raw Parquet write: %d rows, %d bytes in %.2fs", len(combined_table), parquet_path.stat().st_size, time.perf_counter() - write_started_at)

    return len(combined_table)


def apex_price_timestamps(timestamps: pd.Series) -> pd.Series:
    """Project source timestamps onto the raw Parquet identity.

    Use Arrow's exact cast semantics so the merge and write agree at the
    sub-millisecond boundary. The caller retains the original timestamps for
    metric attachment and diagnostics.

    :param timestamps: Naive UTC timestamps from the ApeX archive or raw file.
    :return: Timestamps at the canonical raw price precision.
    """
    target_type = VaultHistoricalRead.to_pyarrow_schema().field("timestamp").type
    values = pa.Array.from_pandas(timestamps).cast(target_type, safe=False)
    return pd.Series(values.to_numpy(zero_copy_only=False), index=timestamps.index)


def _check_apex_price_keys(frame: pd.DataFrame, keys: list[str], source: str) -> None:
    """Reject missing or ambiguous ApeX identities before choosing a winner.

    :param frame: One input to the merge with canonical comparison columns.
    :param keys: Address and stored timestamp comparison columns.
    :param source: Name of the input for diagnostics.
    :raises ValueError: Missing or repeated identities.
    """
    duplicates = frame.duplicated(keys, keep=False)
    invalid = duplicates | frame[keys[0]].isna() | frame[keys[1]].isna()
    if invalid.any():
        sample = frame.loc[invalid, ["address", "timestamp", keys[1]]].head(8).to_dict("records")
        raise ValueError(f"Invalid or duplicate canonical ApeX prices in {source}: {sample}")


def _merge_native_history_with_existing_parquet(parquet_path: Path, fresh_df: pd.DataFrame, chain_id: int, *, validate_keys: bool = True) -> pd.DataFrame:
    """Merge ApeX, Derive or Neutral Trade prices with their stored history.

    A rebuilt DuckDB or shorter API response may contain only part of a
    vault's history. Preserve existing rows unless the export supplies the
    same vault address and timestamp, in which case the new value wins.

    For ApeX, both inputs use the lower-case address and the timestamp
    truncated to the raw Parquet millisecond. Each input must contain at most
    one row per key. A match across inputs is a replay or correction.

    Only the selected synthetic chain partition is read into Pandas. The
    complete Parquet file stays in Arrow form for the later atomic write.

    :param parquet_path: Existing raw price Parquet path.
    :param fresh_df: Non-empty frame with the shared raw price columns,
        including string ``address`` and naive UTC ``timestamp``.
    :param chain_id: Synthetic chain partition to read.
    :param validate_keys: Check for ambiguous Derive and Solana inputs in incremental mode.
        ApeX always checks both inputs at the stored timestamp precision.
    :return: Combined rows for the native partition.
    """
    assert not fresh_df.empty, "A non-empty native frame is required"
    if parquet_path.exists():
        existing_table = pq.read_table(parquet_path, filters=[("chain", "=", chain_id)])
        existing_df = existing_table.to_pandas() if existing_table.num_rows else None
    else:
        existing_df = None

    if existing_df is None and chain_id != APEX_CHAIN_ID:
        return fresh_df

    combined = pd.concat([existing_df, fresh_df], ignore_index=True) if existing_df is not None else fresh_df.copy()
    if chain_id == SOLANA_DATASET_CHAIN_ID:
        address_keys = {address: VaultSpec(chain_id, address).vault_address for address in combined["address"].unique()}
        combined["_canonical_address"] = combined["address"].map(address_keys)
    else:
        combined["_canonical_address"] = combined["address"].str.lower()

    keys = ["_canonical_address", "timestamp"]
    temporary_columns = ["_canonical_address"]
    existing_rows = len(existing_df) if existing_df is not None else 0
    if chain_id == APEX_CHAIN_ID:
        # Comparing source microseconds directly to a stored millisecond would append
        # a replay as a new price. The subsequent Arrow cast would give both the same key.
        combined["_stored_timestamp"] = apex_price_timestamps(combined["timestamp"])
        keys = ["_canonical_address", "_stored_timestamp"]
        temporary_columns.append("_stored_timestamp")
        _check_apex_price_keys(combined.iloc[existing_rows:], keys, "ApeX source")
        if existing_rows:
            _check_apex_price_keys(combined.iloc[:existing_rows], keys, "stored history")
    elif validate_keys:
        # Overlap across the two inputs is a correction. Duplicates within
        # either input make the result depend on physical row order.
        for source in (combined.iloc[:existing_rows], combined.iloc[existing_rows:]):
            if source.duplicated(keys).any():
                raise ValueError(f"Duplicate canonical native prices on chain {chain_id}")
    return combined.drop_duplicates(subset=keys, keep="last").drop(columns=temporary_columns).reset_index(drop=True)


def merge_native_protocols(  # noqa: PLR0914 - one established coordinator owns all native inputs
    *,
    merge_hypercore: bool = False,
    merge_grvt: bool = False,
    merge_lighter: bool = False,
    merge_hibachi: bool = False,
    merge_apex: bool = False,
    merge_derive_v3: bool = False,
    merge_neutral_trade: bool = False,
    neutral_trade_vault_ids: set[str] | None = None,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    hyperliquid_hf_db_path: Path | None = None,
    vault_db_path: Path | None = None,
    permission_history_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
    hibachi_db_path: Path | None = None,
    apex_db_path: Path | None = None,
    derive_v3_db_path: Path | None = None,
    neutral_trade_db_path: Path | None = None,
    incremental: bool = False,
) -> dict[str, bool]:
    """Merge native protocol price data into the uncleaned parquet in one pass.

    Must run before cleaning so that native protocol data goes through
    the same cleaning pipeline as EVM vaults.

    For Hypercore, both the daily and HF DuckDB databases are always
    merged together so that switching between modes never loses
    historical data. All enabled native sources are collected before the
    existing parquet is read, then their chain partitions are replaced and
    the result is written once. For ApeX and Derive, new rows replace only matching
    vault addresses and timestamps. ApeX matches at the raw Parquet's millisecond
    precision and rejects duplicate keys within either input. Both protocols retain
    unmatched stored prices.
    This preserves history when an API response or rebuilt DuckDB contains
    fewer observations than the existing Parquet file.

    An unavailable, empty, or failed source leaves its existing chain
    partition untouched. This preserves the previous per-source failure
    semantics and prevents a transient database failure from deleting
    historical native-protocol prices.

    Hypercore also exports its independent permission-history Parquet and
    restores missing catalogue entries from both scanner owners before
    cleaning. Existing curated metadata is retained. If either step fails,
    the previous Hypercore price partition is retained with a failed merge
    status, rather than publishing fresh prices with incomplete permissions.

    :param merge_hypercore: Merge Hyperliquid native (Hypercore) vault data
    :param merge_grvt: Merge GRVT native vault data
    :param merge_lighter: Merge Lighter native pool data
    :param merge_hibachi: Merge Hibachi native vault data
    :param merge_apex: Merge ApeX native vault data
    :param merge_neutral_trade: Merge admitted Neutral Trade native observations.
    :param neutral_trade_vault_ids: Optional fixed inventory for numbered onboarding.
    :param merge_derive_v3: Merge Derive v3 mainnet native vault data
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the daily Hyperliquid DuckDB path
    :param hyperliquid_hf_db_path: Override for the HF Hyperliquid DuckDB path
    :param vault_db_path: Shared metadata pickle; defaults beside the raw Parquet.
    :param permission_history_path: Permission-history output; defaults beside the raw Parquet.
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :param hibachi_db_path: Override for the Hibachi DuckDB path
    :param apex_db_path: Override for the ApeX DuckDB path
    :param neutral_trade_db_path: Private Neutral Trade archive override.
    :param derive_v3_db_path: Override for the Derive v3 mainnet DuckDB path
    :param incremental: Skip scopes whose published source revision matches. The scanner enables this by default. Direct migration callers can bootstrap a raw file with a full merge.
    :return: Dictionary mapping step name to success boolean
    """
    parquet_path = uncleaned_parquet_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    started_at = time.perf_counter()
    steps: dict[str, bool] = {}
    replacements: dict[int, pd.DataFrame] = {}
    remove_chain_ids: set[int] = set()
    replacement_address_patterns: dict[int, set[str]] = {}
    perp_snapshots: list[pd.DataFrame] = []

    enabled = {
        "hypercore": merge_hypercore,
        "grvt": merge_grvt,
        "lighter": merge_lighter,
        "hibachi": merge_hibachi,
        "apex": merge_apex,
        "derive-v3": merge_derive_v3,
        "neutral-trade": merge_neutral_trade,
    }
    source_paths = {
        "hypercore": (hyperliquid_db_path or HYPERLIQUID_DAILY_METRICS_DATABASE, hyperliquid_hf_db_path or HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE),
        "grvt": (grvt_db_path or GRVT_DAILY_METRICS_DATABASE,),
        "lighter": (lighter_db_path or LIGHTER_DAILY_METRICS_DATABASE,),
        "hibachi": (hibachi_db_path or HIBACHI_DAILY_METRICS_DATABASE,),
        "apex": (apex_db_path or APEX_METRICS_DATABASE,),
        "derive-v3": (derive_v3_db_path or DERIVE_V3_MAINNET_DATABASE,),
        "neutral-trade": (neutral_trade_db_path or NEUTRAL_TRADE_DATABASE,),
    }
    if incremental and any(enabled.values()) and not parquet_path.is_file():
        logger.warning("Native merge deferred: restore the combined raw Parquet or run EVM price collection first. For a new native-only dataset, initialise it once with INCREMENTAL_POST_PROCESSING=false.")
        return {f"{protocol}-price-merge": False for protocol, active in enabled.items() if active}
    applied = _read_native_revisions(parquet_path) if incremental and any(enabled.values()) else {}
    registry = embed_perp_capability_registry(pa.schema([]), PERP_DEX_CAPABILITY_REGISTRY).metadata
    registry_version = sorted((key.decode(), value.decode()) for key, value in registry.items())
    registry_version = [list(item) for item in registry_version]
    revisions = {}
    captured_files = {}
    catalogue_path = vault_db_path or parquet_path.parent / "vault-metadata-db.pickle"
    permissions_path = permission_history_path or parquet_path.parent / PERMISSION_FILENAME
    lighter_scopes = None
    skipped = 0
    for protocol, active in enabled.items():
        if not incremental or not active:
            continue
        paths = source_paths[protocol]
        captured_files[protocol] = [duckdb_revision(path) for path in paths]
        previous = applied.get(protocol)
        previous_files = previous[2] if isinstance(previous, list) and len(previous) >= 3 else []
        missing_committed_source = any(before[0] is not None and after[0] is None for before, after in zip(previous_files, captured_files[protocol]))
        if missing_committed_source or not any(path.is_file() for path in paths):
            enabled[protocol] = False
            steps[f"{protocol}-price-merge"] = not missing_committed_source and not (protocol == "lighter" and any(key.startswith("lighter/") for key in applied))
            logger.log(logging.INFO if steps[f"{protocol}-price-merge"] else logging.WARNING, "Native merge source missing for %s; retained its existing rows", protocol)
            continue
        if protocol == "lighter":
            try:
                scoped = _lighter_revisions(paths[0])
            except duckdb.Error:
                logger.warning("Could not read Lighter source revisions; retrying the full source")
                scoped = {}
            if scoped:
                lighter_scopes = []
                for deployment in LIGHTER_DEPLOYMENTS:
                    key = f"lighter/{deployment.slug}"
                    revision = [NATIVE_MERGE_VERSION, registry_version, str(paths[0].resolve()), duckdb.__version__, scoped[deployment.slug]]
                    revisions[key] = revision
                    if revision != applied.get(key):
                        lighter_scopes.append(deployment)
                    else:
                        skipped += 1
                if not lighter_scopes:
                    enabled[protocol] = False
                    steps["lighter-price-merge"] = True
                continue
        revision = [NATIVE_MERGE_VERSION, registry_version, captured_files[protocol]]
        revisions[protocol] = revision
        # A fixed inventory is used by standalone onboarding. Include it when
        # the caller explicitly enables revision checks for that invocation.
        if protocol == "neutral-trade":
            revision.append(sorted(neutral_trade_vault_ids) if neutral_trade_vault_ids is not None else None)
        if protocol == "hypercore" and isinstance(previous, list) and len(previous) == 4 and revision == previous[:3] and isinstance(previous[3], dict):
            side = previous[3]
            if catalogue_path.is_file() and permissions_path.is_file() and file_revision(permissions_path) == side.get("permissions"):
                try:
                    catalogue_matches = file_revision(catalogue_path) == side["catalogue"] or set(side["vaults"]).issubset(spec.vault_address for spec in VaultDatabase.read(catalogue_path).rows if spec.chain_id == HYPERCORE_CHAIN_ID)
                except (OSError, EOFError, pickle.UnpicklingError):
                    catalogue_matches = False
                if catalogue_matches:
                    revision.append(side)
        if revision == applied.get(protocol) and (protocol != "hypercore" or len(revision) == 4):
            enabled[protocol] = False
            steps[f"{protocol}-price-merge"] = True
            skipped += 1

    logger.info("Native merge revision checks: %d unchanged scopes skipped", skipped)
    published_scopes = set()

    if enabled["hypercore"]:
        try:
            logger.info("Merging Hypercore prices into uncleaned parquet")
            daily_path, hf_path = source_paths["hypercore"]
            daily_db = None
            hf_db = None
            try:
                if daily_path.exists():
                    daily_db = HyperliquidDailyMetricsDatabase(daily_path, read_only=incremental)
                if hf_path.exists():
                    hf_db = HyperliquidHighFreqMetricsDatabase(hf_path, read_only=incremental)
                if daily_db is None and hf_db is None:
                    logger.warning("No Hyperliquid DuckDB databases found")
                    hypercore_df = pd.DataFrame()
                else:
                    hypercore_df = build_hypercore_prices_dataframe(daily_db=daily_db, hf_db=hf_db)
                    for owner in (db for db in (daily_db, hf_db) if db is not None):
                        catalogue = merge_into_vault_database(owner, catalogue_path, only_missing=True)
                    export_hypercore_permission_history(permissions_path, daily_db=daily_db, hf_db=hf_db)
                    if incremental:
                        revisions["hypercore"].append({"permissions": file_revision(permissions_path), "catalogue": file_revision(catalogue_path), "vaults": sorted(spec.vault_address for spec in catalogue.rows if spec.chain_id == HYPERCORE_CHAIN_ID)})
                    if daily_db is not None:
                        _append_perp_metric_snapshots(daily_db, perp_snapshots)
                    if hf_db is not None:
                        _append_perp_metric_snapshots(hf_db, perp_snapshots)
            finally:
                if daily_db is not None:
                    daily_db.close()
                if hf_db is not None:
                    hf_db.close()

            if hypercore_df.empty:
                logger.warning("No Hyperliquid data to merge from either database")
            else:
                replacements[HYPERCORE_CHAIN_ID] = hypercore_df
            logger.info("Hypercore price merge: %d fresh Hyperliquid price entries", len(hypercore_df))
            steps["hypercore-price-merge"] = True
        except Exception:
            logger.exception("Hypercore price merge failed")
            steps["hypercore-price-merge"] = False

    if enabled["grvt"]:
        try:
            logger.info("Merging GRVT prices into uncleaned parquet")
            db = GRVTDailyMetricsDatabase(source_paths["grvt"][0], read_only=incremental)
            try:
                grvt_df = build_grvt_prices_dataframe(db)
                _append_perp_metric_snapshots(db, perp_snapshots)
            finally:
                db.close()
            if grvt_df.empty:
                logger.warning("No GRVT data to merge")
            else:
                replacements[GRVT_CHAIN_ID] = grvt_df
            logger.info("GRVT price merge: %d fresh GRVT price entries", len(grvt_df))
            steps["grvt-price-merge"] = True
        except Exception:
            logger.exception("GRVT price merge failed")
            steps["grvt-price-merge"] = False

    if enabled["lighter"]:
        try:
            logger.info("Merging Lighter prices into uncleaned parquet")
            db = LighterDailyMetricsDatabase(source_paths["lighter"][0], read_only=incremental and lighter_scopes is not None)
            try:
                if lighter_scopes is None:
                    lighter_df = build_lighter_prices_dataframe(db)
                    _append_perp_metric_snapshots(db, perp_snapshots)
                else:
                    frames = []
                    for deployment in lighter_scopes:
                        frame = build_lighter_prices_dataframe(db, deployment=deployment.slug)
                        if not frame.empty:
                            frames.append(frame)
                            _append_perp_metric_snapshots(db, perp_snapshots, deployment=deployment.slug)
                    lighter_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            finally:
                db.close()
            if lighter_df.empty:
                logger.warning("No Lighter data to merge")
            else:
                fresh_deployments = get_lighter_price_deployments(lighter_df)
                published_scopes.update(f"lighter/{deployment.slug}" for deployment in fresh_deployments)
                for chain_id, deployment_df in lighter_df.groupby("chain"):
                    replacements[int(chain_id)] = deployment_df
                    # Both Lighter deployments share chain 9998 but are scanned
                    # independently. Restrict replacement to deployment address
                    # namespaces present in this fresh export, preventing a
                    # partial scan from deleting the other deployment's data.
                    replacement_address_patterns[int(chain_id)] = {deployment.pool_address_pattern for deployment in fresh_deployments}
                # The short-lived 9996 partition contains Robinhood only. Do
                # not remove it until fresh Robinhood data is available; an
                # Ethereum-only scan is not a valid replacement for it.
                if LIGHTER_ROBINHOOD in fresh_deployments:
                    remove_chain_ids.add(LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID)
            logger.info("Lighter price merge: %d fresh Lighter price entries", len(lighter_df))
            steps["lighter-price-merge"] = True
        except Exception:
            logger.exception("Lighter price merge failed")
            steps["lighter-price-merge"] = False

    if enabled["hibachi"]:
        try:
            logger.info("Merging Hibachi prices into uncleaned parquet")
            db = HibachiDailyMetricsDatabase(source_paths["hibachi"][0], read_only=incremental)
            try:
                hibachi_df = build_hibachi_prices_dataframe(db)
                _append_perp_metric_snapshots(db, perp_snapshots)
            finally:
                db.close()
            if hibachi_df.empty:
                logger.warning("No Hibachi data to merge")
            else:
                replacements[HIBACHI_CHAIN_ID] = hibachi_df
            logger.info("Hibachi price merge: %d fresh Hibachi price entries", len(hibachi_df))
            steps["hibachi-price-merge"] = True
        except Exception:
            logger.exception("Hibachi price merge failed")
            steps["hibachi-price-merge"] = False

    if enabled["apex"]:
        try:
            logger.info("Merging ApeX prices into uncleaned parquet")
            db = ApexMetricsDatabase(source_paths["apex"][0], read_only=incremental)
            try:
                apex_df = build_apex_prices_dataframe(db)
                _append_perp_metric_snapshots(db, perp_snapshots)
            finally:
                db.close()
            if apex_df.empty:
                logger.warning("No ApeX data to merge")
            else:
                replacements[APEX_CHAIN_ID] = _merge_native_history_with_existing_parquet(parquet_path, apex_df, APEX_CHAIN_ID, validate_keys=incremental)
            logger.info("ApeX price merge: %d fresh ApeX price entries", len(apex_df))
            steps["apex-price-merge"] = True
        except Exception:
            logger.exception("ApeX price merge failed")
            steps["apex-price-merge"] = False

    if enabled["derive-v3"]:
        try:
            d_db_path = source_paths["derive-v3"][0]
            if not d_db_path.exists():
                logger.info("No Derive v3 mainnet DuckDB to merge at %s", d_db_path)
            else:
                db = DeriveV3VaultDatabase(d_db_path, read_only=incremental)
                try:
                    derive_df = build_derive_v3_prices_dataframe(db)
                    _append_perp_metric_snapshots(db, perp_snapshots)
                finally:
                    db.close()
                if not derive_df.empty:
                    replacements[DERIVE_V3_CHAIN_ID] = _merge_native_history_with_existing_parquet(parquet_path, derive_df, DERIVE_V3_CHAIN_ID, validate_keys=incremental)
                logger.info("Derive v3 price merge: %d fresh mainnet rows", len(derive_df))
            steps["derive-v3-price-merge"] = True
        except Exception:
            logger.exception("Derive v3 price merge failed")
            steps["derive-v3-price-merge"] = False

    if enabled["neutral-trade"]:
        source_path = source_paths["neutral-trade"][0]
        try:
            if source_path.exists():
                with NeutralTradeDatabase(source_path, read_only=incremental) as source:
                    fresh = build_neutral_trade_prices(source, vault_ids=neutral_trade_vault_ids)
                if not fresh.empty:
                    merged = _merge_native_history_with_existing_parquet(parquet_path, fresh, SOLANA_DATASET_CHAIN_ID, validate_keys=incremental)
                    replacements[SOLANA_DATASET_CHAIN_ID] = merged
            else:
                logger.info("No Neutral Trade source database; merge is a no-op")
            steps["neutral-trade-price-merge"] = True
        except (duckdb.Error, OSError, ValueError, pa.ArrowException) as exc:
            if not incremental:
                raise
            logger.warning("Neutral Trade price merge deferred: %s", exc)
            steps["neutral-trade-price-merge"] = False

    scope_chains = {
        "hypercore": HYPERCORE_CHAIN_ID,
        "grvt": GRVT_CHAIN_ID,
        "lighter": LIGHTER_CHAIN_ID,
        "hibachi": HIBACHI_CHAIN_ID,
        "apex": APEX_CHAIN_ID,
        "derive-v3": DERIVE_V3_CHAIN_ID,
        "neutral-trade": SOLANA_DATASET_CHAIN_ID,
    }
    if incremental:
        # An empty builder cannot retire previously published history. Keep
        # its revision pending even if another protocol can publish this tick.
        # Append-style archives (ApeX, Derive and Neutral Trade) can have
        # zero fresh observations, for example after directory revocation.
        for protocol in ("hypercore", "grvt", "hibachi"):
            if enabled[protocol] and protocol in applied and scope_chains[protocol] not in replacements:
                steps[f"{protocol}-price-merge"] = False
        for deployment in lighter_scopes or ():
            key = f"lighter/{deployment.slug}"
            if key in applied and key not in published_scopes:
                steps["lighter-price-merge"] = False

    if incremental and lighter_scopes is None and enabled["lighter"]:
        # A legacy schema was upgraded by its writer. Re-read the now
        # deployment-aware source on the next tick before recording a receipt.
        steps["lighter-price-merge"] = False

    if not replacements:
        logger.info("Native protocol merge stage complete: 0 replacement rows in %.2f seconds", time.perf_counter() - started_at)
        return steps

    non_empty_snapshots = [snapshot for snapshot in perp_snapshots if not snapshot.empty]
    if non_empty_snapshots:
        attach_started_at = time.perf_counter()
        all_perp_snapshots = pd.concat(non_empty_snapshots, ignore_index=True)
        for chain_id, replacement in replacements.items():
            attach_input_rows = len(replacement)
            replacements[chain_id] = attach_perp_metrics_to_price_rows(
                replacement,
                all_perp_snapshots[all_perp_snapshots["chain"] == chain_id],
            )
            logger.info("Perp price attachment chain %s: %d rows in %.2f seconds", chain_id, attach_input_rows, time.perf_counter() - attach_started_at)
            attach_started_at = time.perf_counter()

    published_scopes.update(protocol for protocol, chain_id in scope_chains.items() if enabled[protocol] and chain_id in replacements)
    next_revisions = None
    if incremental:
        # The scanner's writer lock covers source discovery, reads and rename.
        # If an independent writer or a schema upgrade changed a source, retry
        # from that new generation instead of acknowledging a mixed snapshot.
        for protocol in published_scopes & captured_files.keys():
            if [duckdb_revision(path) for path in source_paths[protocol]] != captured_files[protocol]:
                logger.warning("Native source %s changed during merge; retrying publication on the next tick", protocol)
                steps[f"{protocol}-price-merge"] = False
                return {key: False if key.removesuffix("-price-merge") in published_scopes else value for key, value in steps.items()}
        next_revisions = applied | {key: revisions[key] for key in published_scopes if key in revisions}
    try:
        total_rows = _write_native_partitions_to_uncleaned_parquet(
            parquet_path,
            replacements,
            remove_chain_ids=remove_chain_ids,
            replacement_address_patterns=replacement_address_patterns,
            capability_registry=PERP_DEX_CAPABILITY_REGISTRY,
            revisions=next_revisions,
        )
        logger.info(
            "Merged %d native protocol chain partitions (%d fresh rows, %d total rows) into uncleaned %s in one PyArrow parquet write in %.2f seconds",
            len(replacements),
            sum(len(df) for df in replacements.values()),
            total_rows,
            parquet_path,
            time.perf_counter() - started_at,
        )
    except Exception:
        logger.exception("Native protocol batch price merge failed")
        for protocol, chain_id in scope_chains.items():
            if chain_id in replacements:
                steps[f"{protocol}-price-merge"] = False

    return steps


def clean_prices(
    vault_db_path: Path | None = None,
    uncleaned_path: Path | None = None,
    cleaned_path: Path | None = None,
    settlement_db_path: Path | None = None,
) -> bool:
    """Run the price cleaning pipeline.

    Reads uncleaned parquet and writes cleaned parquet.

    .. note::

        ``OSError`` (e.g. ZSTD decompression failure from a corrupted
        parquet file) is deliberately **not** caught here.  A corrupted
        input file is a critical data-integrity issue that must crash the
        pipeline so an operator can investigate and restore from backup.

    :param vault_db_path: Override for the vault database pickle path
    :param uncleaned_path: Override for the uncleaned parquet path
    :param cleaned_path: Override for the cleaned parquet output path
    :param settlement_db_path: Override for the vault settlement DuckDB path
    :return: True if cleaning succeeded
    """
    started_at = time.perf_counter()
    try:
        logger.info("Cleaning vault prices data")
        kwargs = {}
        if vault_db_path is not None:
            kwargs["vault_db_path"] = vault_db_path
        if uncleaned_path is not None:
            kwargs["price_df_path"] = uncleaned_path
        if cleaned_path is not None:
            kwargs["cleaned_price_df_path"] = cleaned_path
        if settlement_db_path is not None:
            kwargs["settlement_db_path"] = settlement_db_path
        resolved_cleaned_path = cleaned_path or get_pipeline_data_dir() / "cleaned-vault-prices-1h.parquet"
        # The crypto bundle runs immediately after this cleaner.  Keep a
        # verified daily derivative beside the hourly output so it can avoid a
        # second read of all stablecoin rows.  The derivative is optional and
        # the bundle has an explicit hourly fallback when it is unavailable.
        kwargs["daily_price_df_path"] = resolved_cleaned_path.with_name("cleaned-vault-prices-1d.parquet")
        generate_cleaned_vault_datasets(**kwargs, logger=logger.info, warning_logger=logger.warning)
        logger.info("Price cleaning complete in %.2f seconds", time.perf_counter() - started_at)
        return True
    except OSError:
        # Corrupted parquet (e.g. "ZSTD decompression failed: Data
        # corruption detected") is a hard failure — the operator must
        # investigate and restore from backup.  Never swallow this.
        raise
    except Exception:
        logger.exception("Clean prices failed")
        return False


def clean_crypto_vault_prices(
    *,
    vault_db_path: Path,
    uncleaned_path: Path,
    cleaned_path: Path,
    cleaned_stablecoin_path: Path,
    cleaned_stablecoin_daily_path: Path | None = None,
    settlement_db_path: Path | None = None,
) -> bool:
    """Build the isolated daily stablecoin/ETH/BTC cleaned Parquet.

    Any error is contained here so the existing stablecoin and public export
    path can complete.  The underlying cleaner still preserves its old output
    file through its temporary-write verification protocol.

    :param vault_db_path:
        Common vault metadata pickle.
    :param uncleaned_path:
        Shared raw price Parquet file.
    :param cleaned_path:
        Isolated crypto daily Parquet destination.
    :param cleaned_stablecoin_path:
        Standard stablecoin-only cleaned Parquet source.
    :param cleaned_stablecoin_daily_path:
        Optional daily sidecar produced by the standard cleaner. Missing or
        unreadable sidecars are handled by the crypto builder's hourly fallback.
    :param settlement_db_path:
        Optional settlement database.
    :return:
        ``True`` if the crypto cleaning phase completed.
    """
    started_at = time.perf_counter()
    try:
        build_crypto_vault_prices(
            vault_db_path=vault_db_path,
            uncleaned_path=uncleaned_path,
            cleaned_path=cleaned_path,
            cleaned_stablecoin_path=cleaned_stablecoin_path,
            cleaned_stablecoin_daily_path=cleaned_stablecoin_daily_path,
            settlement_db_path=settlement_db_path,
        )
        logger.info("Crypto price cleaning complete in %.2f seconds", time.perf_counter() - started_at)
        return True
    except Exception:
        logger.exception("Crypto vault price cleaning failed")
        return False


def calculate_crypto_vault_metadata(
    *,
    vault_db_path: Path,
    paths: CryptoVaultPaths,
    exchange_rate_parquet_path: Path,
) -> dict[str, Any] | None:
    """Calculate isolated crypto metadata while containing phase failures.

    :param vault_db_path:
        Common vault metadata pickle.
    :param paths:
        Explicit crypto bundle paths.
    :param exchange_rate_parquet_path:
        Verified exchange-rate snapshot shared with the R2 data export.
    :return:
        Metadata document, or ``None`` after a contained failure.
    """
    try:
        return build_crypto_vault_metadata(
            vault_db_path=vault_db_path,
            cleaned_price_path=paths.cleaned_price_path,
            metadata_path=paths.metadata_path,
            sticky_state_path=paths.sticky_state_path,
            exchange_rate_parquet_path=exchange_rate_parquet_path,
        )
    except Exception:
        logger.exception("Crypto vault metadata calculation failed")
        return None


def export_crypto_vault_bundle(paths: CryptoVaultPaths, metadata: dict[str, Any]) -> bool:
    """Publish the prepared private crypto bundle without propagating errors.

    :param paths:
        Explicit crypto bundle paths.
    :param metadata:
        Prepared metadata document.
    :return:
        ``True`` after successful upload and backup attempt.
    """
    try:
        return publish_crypto_vault_bundle(paths, metadata)
    except Exception:
        logger.exception("Crypto vault bundle export failed")
        return False


def export_crypto_exchange_rate_parquet(parquet_path: Path) -> bool:
    """Publish the crypto bundle's rate snapshot without stopping public work.

    :param parquet_path:
        Verified local exchange-rate Parquet used for USD metric calculation.
    :return:
        ``True`` when the private rate snapshot and its backup were published.
    """
    try:
        publish_exchange_rate_parquet_to_alternative_bucket(parquet_path)
        return True
    except (AssertionError, FileNotFoundError, OSError, R2OperationError):
        logger.exception("Crypto exchange-rate Parquet export failed")
        return False


def export_sparklines(
    *,
    prices_path: Path | None = None,
    vault_db_path: Path | None = None,
    state_path: Path | None = None,
) -> bool:
    """Export sparkline images to R2 through the typed library entry point.

    The scanner already holds the shared ``scan-pipeline`` writer lock when
    this function is called from post-processing. The standalone script owns
    the same lock around its direct library call.

    :param prices_path:
        Current crypto daily price Parquet.
    :param vault_db_path:
        Current vault metadata database.
    :param state_path:
        Persistent sparkline publication state.
    :return:
        True if export succeeds.
    """
    try:
        logger.info("Creating sparkline images")
        data_dir = get_pipeline_data_dir()
        result = run_sparkline_export(
            data_dir=data_dir,
            prices_path=prices_path,
            vault_db_path=vault_db_path,
            state_path=state_path,
        )
        if not result.success:
            logger.error("Sparkline export completed with %d failed vaults", result.counters["failed"])
            return False
        logger.info("Sparkline export complete")
        return True
    except (EOFError, KeyError, ImportError, OSError, pickle.UnpicklingError, pa.ArrowException, R2OperationError, RuntimeError, TypeError, ValueError):
        logger.exception("Export sparklines failed")
        return False


def export_protocol_metadata() -> bool:
    """Export protocol/stablecoin metadata and logos to R2.

    :return: True if export succeeded
    """
    try:
        logger.info("Exporting protocol metadata files")
        spec = importlib.util.spec_from_file_location("export_protocol_metadata", "scripts/erc-4626/export-protocol-metadata.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        logger.info("Protocol metadata export complete")
        return True
    except Exception:
        logger.exception("Export protocol metadata failed")
        return False


def export_data_files(
    exchange_rate_parquet_path: Path | None = None,
    exchange_rate_parquet_error: Exception | None = None,
) -> bool:
    """Export database files (parquet, pickle, DuckDB) to R2.

    :param exchange_rate_parquet_path:
        Optional exact snapshot used by the crypto USD metrics phase.
    :param exchange_rate_parquet_error:
        Earlier snapshot materialisation error. The export uploads unrelated
        data and then reports this phase as failed without publishing stale
        exchange-rate Parquet.
    :return: True if export succeeded
    """
    try:
        logger.info("Exporting data files")
        spec = importlib.util.spec_from_file_location("export_data_files", "scripts/erc-4626/export-data-files.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main(
            exchange_rate_parquet_path=exchange_rate_parquet_path,
            exchange_rate_parquet_error=exchange_rate_parquet_error,
        )
        logger.info("Data file export complete")
        return True
    except R2RetryableOperationError as exc:
        logger.warning("Data file export deferred after retryable R2 failure: %s", exc)
        return False
    except Exception:
        logger.exception("Export data files failed")
        return False


def export_sample_files(
    *,
    skip_parquet_sample: bool = False,
    skip_json_sample: bool = False,
) -> bool:
    """Export Ethereum-only sample data files to R2.

    Generates filtered sample versions of the cleaned parquet and
    top-vaults JSON for free download, then uploads to the primary
    (public) R2 bucket only — deliberately skips the alternative
    (private) bucket.

    :param skip_parquet_sample:
        Skip the parquet sample generation (e.g. when the
        cleaned parquet was not generated this run).

    :param skip_json_sample:
        Skip the JSON sample generation (e.g. when the
        top-vaults JSON was not generated this run).

    :return: True if export succeeded
    """
    try:
        logger.info("Exporting sample data files")
        export_sample_files_to_r2(
            skip_parquet_sample=skip_parquet_sample,
            skip_json_sample=skip_json_sample,
        )
        logger.info("Sample data file export complete")
        return True
    except Exception:
        logger.exception("Export sample files failed")
        return False


def validate_top_vaults_config(*, skip_top_vaults: bool = False) -> None:
    """Fail-fast pre-flight check for the top-vaults JSON R2 upload.

    Both the long-running scanner and the standalone debug entry point
    call this helper at startup, *before* any scanning or cleaning
    happens, so that a misconfigured production host is caught
    immediately instead of hours later when :py:func:`export_top_vaults_json`
    is finally reached.

    The escape hatch is ``SKIP_TOP_VAULTS=true`` — if the caller has
    explicitly disabled the step, no validation is performed.

    :param skip_top_vaults:
        When ``True``, this check is a no-op. Mirrors the
        ``SKIP_TOP_VAULTS`` env var used elsewhere in the pipeline.

    :raise RuntimeError:
        If any required ``R2_TOP_VAULTS_*`` env var is missing and the
        step is not explicitly skipped.
    """
    if skip_top_vaults:
        logger.info("SKIP_TOP_VAULTS=true — skipping R2 top-vaults config validation")
        return

    missing_name = next((name for name in _R2_TOP_VAULTS_REQUIRED_ENV_VARS if not os.environ.get(name)), None)
    if missing_name:
        raise RuntimeError(f"R2 top-vaults upload is not configured: {missing_name} is not set. Either set the R2_TOP_VAULTS_* env vars or set SKIP_TOP_VAULTS=true to explicitly disable the top-vaults JSON export.")

    alt_bucket = os.environ.get("R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME")
    if alt_bucket:
        logger.info("R2 top-vaults alternative (private) bucket configured: %s", alt_bucket)


def export_top_vaults_json(  # noqa: PLR0914 - R2 export settings are resolved together
    vault_db_path: Path | None = None,
    cleaned_path: Path | None = None,
    output_path: Path | None = None,
    core3_db_path: Path | None = None,
    feed_db_path: Path | None = None,
) -> bool:
    """Generate the top-vaults lifetime-metrics JSON and upload to R2.

    Runs :py:mod:`eth_defi.vault.top_vaults_json` against the
    active pipeline data directory to produce
    ``top_vaults_by_chain.json``, then uploads the result to the
    primary (public) ``R2_TOP_VAULTS_*`` bucket and, if configured, also to the
    alternative (private) bucket via ``R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME``.

    This is a drop-in replacement for the standalone ``vault-analysis``
    docker image: the JSON generation and the R2 upload now both live
    inside the scanner post-processing pipeline.

    The JSON generation includes the best-effort vault export cleanup hooks.
    An unavailable Yearn offchain registry is contained by the hook runner;
    it does not abort this post-processing process or prevent unrelated export
    steps from running.

    Honours ``UPLOAD_PREFIX`` for test isolation — with
    ``UPLOAD_PREFIX=test-`` the object key becomes
    ``test-top_vaults_by_chain.json`` in both buckets.

    :param vault_db_path:
        Override for the vault metadata pickle path. Defaults to
        ``get_pipeline_data_dir() / "vault-metadata-db.pickle"``.

    :param cleaned_path:
        Override for the cleaned vault prices parquet. Defaults to
        ``get_pipeline_data_dir() / "cleaned-vault-prices-1h.parquet"``.

    :param output_path:
        Override for the generated JSON file. Defaults to
        ``get_pipeline_data_dir() / "top_vaults_by_chain.json"``. The filename
        is intentionally kept identical to the existing public URL
        ``https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json``.

    :param core3_db_path:
        Override for the Core3 risk intelligence DuckDB path. When ``None``,
        ``top_vaults_json.main()`` auto-discovers from
        ``CORE3_DATABASE_PATH`` env var or the default constant.

    :param feed_db_path:
        Override for the vault post feed DuckDB path, used to enrich the
        export with curator metadata and recent feed entries. When
        ``None``, ``top_vaults_json.main()`` auto-discovers from
        ``FEED_DB_PATH``/``DB_PATH`` env vars or the default constant via
        :py:func:`~eth_defi.feed.database.resolve_feed_database_path`.

    :return:
        ``True`` if the JSON was generated and uploaded, ``False`` on
        any failure. Matches the behaviour of the other ``export_*``
        helpers so the caller can log and continue.
    """
    bucket_name = "<unset>"
    endpoint_url = "<unset>"
    object_key = "<unset>"
    access_key_id = ""
    public_url = ""

    try:
        validate_top_vaults_config(skip_top_vaults=False)

        base = get_pipeline_data_dir()
        if vault_db_path is None:
            vault_db_path = base / "vault-metadata-db.pickle"
        if cleaned_path is None:
            cleaned_path = base / "cleaned-vault-prices-1h.parquet"
        if output_path is None:
            output_path = base / "top_vaults_by_chain.json"

        logger.info("Generating top vaults JSON at %s", output_path)
        output_data = top_vaults_json.main(
            data_dir=base,
            vault_db_path=vault_db_path,
            parquet_path=cleaned_path,
            output_path=output_path,
            core3_db_path=core3_db_path,
            feed_db_path=feed_db_path,
        )

        bucket_name = os.environ["R2_TOP_VAULTS_BUCKET_NAME"]
        access_key_id = os.environ["R2_TOP_VAULTS_ACCESS_KEY_ID"]
        secret_access_key = os.environ["R2_TOP_VAULTS_SECRET_ACCESS_KEY"]
        endpoint_url = os.environ["R2_TOP_VAULTS_ENDPOINT_URL"]
        public_url = os.environ.get("R2_TOP_VAULTS_PUBLIC_URL", "")
        upload_prefix = os.environ.get("UPLOAD_PREFIX", "")
        object_key = f"{upload_prefix}top_vaults_by_chain.json"

        s3_client = create_r2_client(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

        # This public feed has downstream operational consumers, so retain its
        # established primary and optional alternative publication paths.
        alt_bucket_name = os.environ.get("R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME")
        uploads_ok = _upload_top_vaults_json_to_configured_buckets(
            s3_client=s3_client,
            output_path=output_path,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            object_key=object_key,
            access_key_id=access_key_id,
            public_url=public_url,
            alt_bucket_name=alt_bucket_name,
        )
        if uploads_ok:
            logger.info("Top vaults JSON export complete")
            metadata = output_data.get("metadata", {}) if output_data else {}
            version = metadata.get("version", {})
            logger.info(
                "VAULT_JSON_PUBLISHED: object=%s generated_at=%s commit_hash=%s",
                object_key,
                output_data.get("generated_at") if output_data else None,
                version.get("commit_hash"),
            )
        else:
            logger.warning("Top vaults JSON export completed with one or more upload failures")
        return uploads_ok
    except Exception:
        logger.exception(
            "Export top vaults JSON failed — bucket=%s, endpoint=%s, object_key=%s, access_key_id=%s...%s",
            bucket_name,
            endpoint_url,
            object_key,
            access_key_id[:4] if access_key_id else "<empty>",
            access_key_id[-4:] if access_key_id else "",
        )
        return False


def _clean_incrementally(
    raw_path: Path,
    vault_db_path: Path,
    settlement_path: Path,
    hourly_path: Path,
    crypto_path: Path,
) -> tuple[bool, bool]:
    """Reconcile the cleaned products and record the verified generations.

    The checkpoint follows all three outputs. A crash during replacement or
    an out-of-band rewrite invalidates the checkpoint's output tuple and
    forces a full rebuild before targeted replacements can resume.

    :param raw_path: Combined raw Parquet.
    :param vault_db_path: Metadata catalogue.
    :param settlement_path: Settlement database.
    :param hourly_path: Public hourly prices.
    :param crypto_path: Private daily prices.
    :return: Public and crypto cleaning success flags.
    """
    if not raw_path.is_file() or not vault_db_path.is_file():
        logger.warning("Price cleaning deferred until the combined prices and catalogue are available")
        return False, False
    started_at = time.perf_counter()
    daily_path = hourly_path.with_name("cleaned-vault-prices-1d.parquet")
    output_paths = (hourly_path, daily_path, crypto_path)
    checkpoint_path = hourly_path.with_suffix(".cleaning.json")
    previous = read_publication_state(checkpoint_path)
    outputs = [file_revision(path) for path in output_paths]
    inputs = cleaning_inputs(raw_path, vault_db_path, settlement_path)
    outputs_match = all(outputs) and previous.get("outputs") == outputs
    if outputs_match and previous.get("inputs") == inputs:
        logger.info("Vault price cleaning skipped: sources and outputs unchanged")
        return True, True

    try:
        snapshot = cleaning_snapshot(raw_path, vault_db_path, settlement_path)
        changed = changed_histories(previous.get("snapshot", {}), snapshot) if outputs_match else None
        if changed is None:
            logger.info("Running full price cleaning to establish a current output baseline")
            if not clean_prices(vault_db_path, raw_path, hourly_path, settlement_path):
                return False, False
            crypto_ok = clean_crypto_vault_prices(
                vault_db_path=vault_db_path,
                uncleaned_path=raw_path,
                cleaned_path=crypto_path,
                cleaned_stablecoin_path=hourly_path,
                cleaned_stablecoin_daily_path=daily_path,
                settlement_db_path=settlement_path,
            )
            if not crypto_ok:
                return True, False
            if not daily_path.is_file() or daily_path.stat().st_mtime_ns < hourly_path.stat().st_mtime_ns:
                logger.warning("Daily sidecar needs repair; retaining the hourly fallback")
                return True, False
        else:
            crypto_ok = replace_changed_histories(
                snapshot,
                *changed,
                raw_path=raw_path,
                vault_db_path=vault_db_path,
                settlement_path=settlement_path,
                hourly_path=hourly_path,
                daily_path=daily_path,
                crypto_path=crypto_path,
            )
            if not crypto_ok:
                return True, False
        if cleaning_inputs(raw_path, vault_db_path, settlement_path) != inputs:
            logger.warning("Cleaning inputs changed during publication; deferring acknowledgement")
            return False, False
        published = [file_revision(path) for path in output_paths]
        write_publication_state(checkpoint_path, {"inputs": inputs, "outputs": published, "snapshot": snapshot})
        logger.info("Vault cleaner finished in %.2fs. Output sizes before and after: %s -> %s bytes", time.perf_counter() - started_at, [item[3] if item else 0 for item in outputs], [item[3] for item in published])
        return True, True
    except (duckdb.Error, AssertionError, OSError, ValueError, KeyError, RuntimeError, TypeError, pa.ArrowException) as exc:
        logger.warning("Incremental price cleaning deferred: %s", exc)
        return False, False


def run_post_processing(  # noqa: PLR0914 - orchestration keeps stage options explicit
    *,
    scan_hypercore: bool = False,
    scan_grvt: bool = False,
    scan_lighter: bool = False,
    scan_hibachi: bool = False,
    scan_apex: bool = False,
    scan_derive_v3: bool = False,
    scan_neutral_trade: bool = False,
    skip_cleaning: bool = False,
    skip_top_vaults: bool = False,
    skip_sparklines: bool = False,
    skip_metadata: bool = False,
    skip_data: bool = False,
    skip_samples: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    hyperliquid_hf_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
    hibachi_db_path: Path | None = None,
    apex_db_path: Path | None = None,
    derive_v3_db_path: Path | None = None,
    neutral_trade_db_path: Path | None = None,
    vault_db_path: Path | None = None,
    cleaned_path: Path | None = None,
    settlement_db_path: Path | None = None,
    core3_db_path: Path | None = None,
    feed_db_path: Path | None = None,
    crypto_vaults_dir: Path | None = None,
    price_scan_state_path: Path | None = None,
    incremental: bool = True,
    incremental_cleaning: bool = True,
    publication_retry: bool = False,
) -> dict[str, bool]:
    """Run full post-processing pipeline after chain scans complete.

    The pipeline merges native data, cleans public and private prices and
    calculates metadata. It publishes private prices and readiness before
    rendering sparklines, then completes the remaining exports. Crypto failures are recorded
    without preventing later public phases from running.

    :param scan_hypercore: Whether to merge Hypercore data
    :param scan_grvt: Whether to merge GRVT data
    :param scan_lighter: Whether to merge Lighter data
    :param scan_hibachi: Whether to merge Hibachi data
    :param scan_apex: Whether to merge ApeX data
    :param scan_neutral_trade: Include Neutral Trade native REST collection/merge.
    :param scan_derive_v3: Whether to merge Derive v3 mainnet data
    :param skip_cleaning:
        Reuse the existing cleaned stablecoin price files during a manual
        export retry after cleaning has already succeeded for the intended
        snapshot. The crypto cleaner still runs and uses those stablecoin
        prices. If newer raw observations exist, exports such as sparklines
        can use older stablecoin data. Leave this false for scheduled scans.
        Incremental cleaning already reuses unchanged histories and
        recalculates histories that changed.
    :param skip_top_vaults: Skip top-vaults JSON generation and R2 upload
    :param skip_sparklines: Skip sparkline image export to R2
    :param skip_metadata: Skip protocol/stablecoin metadata export to R2
    :param skip_data: Skip data file (parquet, pickle) export to R2
    :param skip_samples: Skip Ethereum-only sample file export to R2
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the daily Hyperliquid DuckDB path
    :param hyperliquid_hf_db_path: Override for the HF Hyperliquid DuckDB path
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :param hibachi_db_path: Override for the Hibachi DuckDB path
    :param apex_db_path: Override for the ApeX DuckDB path
    :param neutral_trade_db_path: Private Neutral Trade archive override.
    :param derive_v3_db_path: Override for the Derive v3 mainnet DuckDB path
    :param vault_db_path: Override for the vault database pickle path
    :param cleaned_path: Override for the cleaned parquet output path
    :param settlement_db_path: Override for the vault settlement DuckDB path
    :param core3_db_path: Override for the Core3 risk intelligence DuckDB path
    :param feed_db_path: Override for the vault post feed DuckDB path (curator metadata and feed entries)
    :param crypto_vaults_dir: Override for the isolated crypto bundle directory.
    :param price_scan_state_path: Price-only scan provenance written by the
        scanner. Defaults next to the cleaned price file.
    :param incremental: Reuse committed native scopes.
    :param incremental_cleaning: Reuse cleaned histories when incremental mode is enabled.
    :param publication_retry: Stop an idle recovery tick before exports if its local inputs still need repair.
    :return: Dictionary mapping step name to success boolean
    """
    timings = ScanTimings()
    steps: dict[str, bool] = {}

    def log_summary() -> None:
        """Include failed steps by name, including when an idle retry ends early."""
        failed = [name for name, ok in steps.items() if not ok]
        logger.info("Post-processing timing ok=%d failed=%d failed_steps=%s %s", len(steps) - len(failed), len(failed), ",".join(failed) or "none", timings)

    data_dir = get_pipeline_data_dir()
    resolved_vault_db_path = vault_db_path or data_dir / "vault-metadata-db.pickle"

    # Merge native protocols.
    merge_results = merge_native_protocols(
        merge_hypercore=scan_hypercore,
        merge_grvt=scan_grvt,
        merge_lighter=scan_lighter,
        merge_hibachi=scan_hibachi,
        merge_apex=scan_apex,
        merge_derive_v3=scan_derive_v3,
        merge_neutral_trade=scan_neutral_trade,
        uncleaned_parquet_path=uncleaned_parquet_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        vault_db_path=resolved_vault_db_path,
        permission_history_path=data_dir / PERMISSION_FILENAME,
        grvt_db_path=grvt_db_path,
        lighter_db_path=lighter_db_path,
        hibachi_db_path=hibachi_db_path,
        apex_db_path=apex_db_path,
        derive_v3_db_path=derive_v3_db_path,
        neutral_trade_db_path=neutral_trade_db_path,
        incremental=incremental,
    )
    steps.update(merge_results)
    native_publication_ok = not incremental or all(merge_results.values())
    timings.finish("native_merge")

    if publication_retry and not native_publication_ok:
        logger.warning("Native history is still pending; ending idle retry before cleaning and exports")
        log_summary()
        return steps

    # The isolated crypto product can fail independently of public publication.
    crypto_paths = resolve_crypto_vault_paths(data_dir, crypto_vaults_dir)
    resolved_cleaned_stablecoin_path = cleaned_path or data_dir / "cleaned-vault-prices-1h.parquet"
    raw_path = uncleaned_parquet_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    if incremental and incremental_cleaning and not skip_cleaning:
        cleaning_ok, crypto_clean_ok = _clean_incrementally(
            raw_path,
            resolved_vault_db_path,
            settlement_db_path or get_default_vault_settlement_database_path(),
            resolved_cleaned_stablecoin_path,
            crypto_paths.cleaned_price_path,
        )
        steps["clean-prices"] = cleaning_ok
    else:
        if skip_cleaning:
            logger.info("Skipping price cleaning (SKIP_CLEANING=true)")
            cleaning_ok = True
        else:
            cleaning_ok = clean_prices(
                vault_db_path=resolved_vault_db_path,
                uncleaned_path=uncleaned_parquet_path,
                cleaned_path=cleaned_path,
                settlement_db_path=settlement_db_path,
            )
            steps["clean-prices"] = cleaning_ok
        crypto_clean_ok = cleaning_ok and clean_crypto_vault_prices(
            vault_db_path=resolved_vault_db_path,
            uncleaned_path=raw_path,
            cleaned_path=crypto_paths.cleaned_price_path,
            cleaned_stablecoin_path=resolved_cleaned_stablecoin_path,
            cleaned_stablecoin_daily_path=resolved_cleaned_stablecoin_path.with_name("cleaned-vault-prices-1d.parquet"),
            settlement_db_path=settlement_db_path,
        )
    steps["clean-crypto-vault-prices"] = crypto_clean_ok
    timings.finish("cleaning")
    if publication_retry and not (cleaning_ok and crypto_clean_ok):
        logger.warning("Price cleaning is still pending; ending idle retry before exports")
        log_summary()
        return steps

    # The crypto USD metrics and R2 data-file export must share one immutable
    # local snapshot. A currency failure is isolated like crypto cleaning: it
    # cannot prevent public exports, but it blocks crypto metadata publication.
    exchange_rate_parquet_path = None
    exchange_rate_parquet_error = None
    try:
        exchange_rate_parquet_path = materialise_exchange_rate_parquet(
            source_path=resolve_exchange_rate_database_path(data_dir),
            destination_path=resolve_exchange_rate_parquet_path(data_dir),
        ).path
        steps["materialise-exchange-rate-parquet"] = True
    except (duckdb.Error, FileNotFoundError, KeyError, OSError, TypeError, ValueError, pa.ArrowException) as exc:
        logger.exception("Exchange-rate Parquet materialisation failed")
        exchange_rate_parquet_error = exc
        steps["materialise-exchange-rate-parquet"] = False

    timings.finish("exchange_rates")
    # Export top vaults JSON, including its best-effort strategy-category
    # aggregate. This depends on cleaned Parquet and must run before the
    # private data-file upload.
    if skip_top_vaults:
        logger.info("Skipping top vaults export (SKIP_TOP_VAULTS=true)")
    elif not cleaning_ok:
        logger.warning("Skipping top vaults export — clean_prices failed, refusing to upload stale data")
        steps["export-top-vaults-json"] = False
    else:
        steps["export-top-vaults-json"] = export_top_vaults_json(
            vault_db_path=vault_db_path,
            cleaned_path=cleaned_path,
            core3_db_path=core3_db_path,
            feed_db_path=feed_db_path,
        )

    timings.finish("listing")
    crypto_metadata = None
    if crypto_clean_ok and cleaning_ok and exchange_rate_parquet_path is not None:
        crypto_metadata = calculate_crypto_vault_metadata(
            vault_db_path=resolved_vault_db_path,
            paths=crypto_paths,
            exchange_rate_parquet_path=exchange_rate_parquet_path,
        )
        steps["calculate-crypto-vault-metadata"] = crypto_metadata is not None
    elif not crypto_clean_ok:
        logger.warning("Skipping crypto metadata — crypto cleaning failed")
        steps["calculate-crypto-vault-metadata"] = False
    else:
        logger.warning("Skipping crypto metadata — no current exchange-rate snapshot or clean price input")
        steps["calculate-crypto-vault-metadata"] = False

    # Publish trading inputs before the potentially lengthy image rendering.
    timings.finish("crypto_metrics")
    if skip_data:
        logger.info("Skipping data file export (SKIP_DATA=true)")
    elif not cleaning_ok:
        logger.warning("Skipping data file export — clean_prices failed, refusing to export stale data")
        steps["export-data-files"] = False
    elif not native_publication_ok:
        logger.warning("Deferring private data export until native prices and permission history are current")
        steps["export-data-files"] = False
    else:
        steps["export-data-files"] = export_data_files(
            exchange_rate_parquet_path=exchange_rate_parquet_path,
            exchange_rate_parquet_error=exchange_rate_parquet_error,
        )

    # Publish readiness only after the cleaned price upload has succeeded.
    # Sample exports below are independent; the manifest commits only private
    # cleaned prices, not every artefact produced by post-processing.
    if steps.get("export-data-files") is True:
        manifest_state_path = price_scan_state_path or (Path(cleaned_path).parent if cleaned_path else data_dir) / "vault-price-scan-state.json"
        try:
            exported_price_path = data_dir / "cleaned-vault-prices-1h.parquet"
            if cleaned_path is not None and cleaned_path.resolve() != exported_price_path.resolve():
                message = "Cannot publish readiness for a cleaned_path override: private export uses the pipeline data directory"
                raise ValueError(message)
            steps["publish-vault-scan-manifest"] = publish_vault_scan_manifest(
                cleaned_price_path=exported_price_path,
                price_scan_state_path=manifest_state_path,
            )
            if os.environ.get("HYPERCORE_PERMISSION_MANIFEST_V2", "false").lower() == "true":
                steps["publish-hypercore-permission-manifest"] = publish_hypercore_permission_manifest(exported_price_path, data_dir / "hypercore-vault-permissions.parquet", manifest_state_path)
        except (RuntimeError, ValueError, OSError, pa.ArrowException):
            logger.exception("Vault scan manifest publication failed")
            steps["publish-vault-scan-manifest"] = False

    # Export sparklines.
    timings.finish("data_and_manifests")
    if skip_sparklines:
        logger.info("Skipping sparkline export (SKIP_SPARKLINES=true)")
    elif not cleaning_ok or not crypto_clean_ok:
        logger.warning("Skipping sparkline export — current public or crypto cleaning failed, refusing to export stale data")
        steps["export-sparklines"] = False
    else:
        steps["export-sparklines"] = export_sparklines(
            prices_path=crypto_paths.cleaned_price_path,
            vault_db_path=resolved_vault_db_path,
            state_path=data_dir / "sparkline-export-state.json",
        )

    # Export protocol metadata. This is not derived from cleaned prices and is
    # always safe to run.
    timings.finish("sparklines")
    if skip_metadata:
        logger.info("Skipping metadata export (SKIP_METADATA=true)")
    else:
        steps["export-protocol-metadata"] = export_protocol_metadata()

    # Export Ethereum-only sample files to the public bucket.
    timings.finish("protocol_metadata")
    if skip_samples:
        logger.info("Skipping sample file export (SKIP_SAMPLES=true)")
    else:
        # Parquet sample requires clean-prices to have succeeded this run.
        parquet_ok = cleaning_ok

        # JSON sample requires BOTH a fresh cleaned parquet (top-vaults JSON is
        # derived from cleaned data) AND a successful top-vaults export.
        json_ok = (cleaning_ok and steps.get("export-top-vaults-json", False)) if not skip_top_vaults else False

        # If neither sample type is eligible, skip the step entirely
        # rather than recording a misleading "OK" for zero work.
        if not parquet_ok and not json_ok:
            logger.warning(
                "Skipping sample export — no eligible source data (clean-prices=%s, export-top-vaults-json=%s)",
                steps.get("clean-prices"),
                steps.get("export-top-vaults-json"),
            )
        else:
            steps["export-sample-files"] = export_sample_files(
                skip_parquet_sample=not parquet_ok,
                skip_json_sample=not json_ok,
            )

    timings.finish("samples")
    crypto_rate_published = bool(steps.get("export-data-files"))
    if not crypto_rate_published and exchange_rate_parquet_path is not None:
        # ``SKIP_DATA=true`` and public-data export failures must not prevent
        # the independent private bundle. Publish the exact local snapshot
        # before making crypto metadata current.
        crypto_rate_published = export_crypto_exchange_rate_parquet(exchange_rate_parquet_path)

    if not native_publication_ok:
        logger.warning("Deferring private crypto publication until native prices are current")
        steps["export-crypto-vault-bundle"] = False
    elif crypto_metadata is None:
        logger.warning("Skipping crypto bundle publication — crypto metadata was not generated")
        steps["export-crypto-vault-bundle"] = False
    elif not crypto_rate_published:
        logger.warning("Skipping crypto bundle publication — exchange-rate snapshot was not published to the private bucket")
        steps["export-crypto-vault-bundle"] = False
    else:
        steps["export-crypto-vault-bundle"] = export_crypto_vault_bundle(crypto_paths, crypto_metadata)

    timings.finish("crypto_upload")
    log_summary()
    return steps
