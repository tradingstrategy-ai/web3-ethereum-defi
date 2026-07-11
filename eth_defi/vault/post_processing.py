"""Post-processing pipeline for vault price data.

Merges native protocol data (Hypercore, GRVT, Lighter) into the
uncleaned parquet, runs the cleaning pipeline, and optionally
uploads results to R2.

Used by both :py:mod:`scan-vaults-all-chains` and
:py:mod:`post-process-prices` scripts.
"""

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from eth_defi.cloudflare_r2 import calculate_bytes_digest, copy_r2_object_daily_backup, create_r2_client, upload_bytes_to_r2, upload_file_to_r2
from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault_data_export import build_raw_prices_dataframe as build_grvt_prices_dataframe
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID, HIBACHI_DAILY_METRICS_DATABASE
from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
from eth_defi.hibachi.vault_data_export import build_raw_prices_dataframe as build_hibachi_prices_dataframe
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE, HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.high_freq_metrics import HyperliquidHighFreqMetricsDatabase
from eth_defi.hyperliquid.vault_data_export import build_hypercore_prices_dataframe
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DAILY_METRICS_DATABASE
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.vault_data_export import build_raw_prices_dataframe as build_lighter_prices_dataframe
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault import top_vaults_json
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, get_pipeline_data_dir

#: Required env vars for the top-vaults JSON R2 upload.
#: See :py:func:`validate_top_vaults_config`.
_R2_TOP_VAULTS_REQUIRED_ENV_VARS = (
    "R2_TOP_VAULTS_BUCKET_NAME",
    "R2_TOP_VAULTS_ACCESS_KEY_ID",
    "R2_TOP_VAULTS_SECRET_ACCESS_KEY",
    "R2_TOP_VAULTS_ENDPOINT_URL",
)

logger = logging.getLogger(__name__)


def _mask_access_key_id(access_key_id: str | None) -> str:
    """Mask an R2 access key ID for safe logging.

    :param access_key_id:
        Raw access key ID.

    :return:
        Masked access key ID.
    """
    if not access_key_id:
        return "<unknown>"
    if len(access_key_id) <= 8:
        return access_key_id
    return f"{access_key_id[:4]}...{access_key_id[-4:]}"


def _upload_top_vaults_json_to_bucket(
    s3_client: Any,
    output_path: Path,
    bucket_name: str,
    endpoint_url: str,
    object_key: str,
    access_key_id: str,
    *,
    public_url: str = "",
    bucket_label: str,
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

    :return:
        ``True`` if the bucket upload succeeded or was skipped as
        unchanged, ``False`` on failure.
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
    # Brotli is an optional dependency — if unavailable, raw upload still succeeds.
    try:
        import brotli

        raw_bytes = output_path.read_bytes()
        compressed = brotli.compress(raw_bytes, quality=11)
        source_digest = calculate_bytes_digest(raw_bytes)

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
        ratio = len(compressed) / len(raw_bytes) * 100 if raw_bytes else 0
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
    except ImportError:
        logger.warning("brotli package not installed — skipping .json.br upload for %s bucket", bucket_label)
        return False
    except Exception:
        logger.exception("Brotli compression/upload failed for %s bucket — raw JSON already uploaded", bucket_label)
        return False

    return True


def _upload_top_vaults_json_to_configured_buckets(
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

    The primary and alternative buckets are attempted independently.
    This means a permission issue on one bucket does not stop the other
    upload attempt or later post-processing steps.

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
    primary_success = _upload_top_vaults_json_to_bucket(
        s3_client=s3_client,
        output_path=output_path,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        object_key=object_key,
        access_key_id=access_key_id,
        public_url=public_url,
        bucket_label="primary",
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


def _write_native_partitions_to_uncleaned_parquet(parquet_path: Path, replacements: dict[int, pd.DataFrame]) -> int:
    """Replace native chain partitions using PyArrow without full pandas conversion.

    The existing file is read as an Arrow table, only the successful native
    chains are removed, and fresh source frames are converted and aligned once.
    The combined table is sorted for compression efficiency, verified in a
    sibling temporary file, and atomically swapped into place.

    :param parquet_path:
        Raw vault-price parquet to update.
    :param replacements:
        Fresh, non-empty source frames keyed by their synthetic chain IDs.
    :return:
        Total row count in the new raw parquet.
    """
    assert replacements, "At least one native chain replacement is required"

    existing_table = pq.read_table(parquet_path) if parquet_path.exists() else None
    schema = _create_native_merge_schema(existing_table.schema if existing_table is not None else None, replacements)
    replacement_tables = [_align_native_merge_table(pa.Table.from_pandas(frame, preserve_index=False), schema) for frame in replacements.values()]
    native_table = pa.concat_tables(replacement_tables)

    if existing_table is not None:
        chain_mask = pc.is_in(existing_table["chain"], value_set=pa.array(list(replacements)))
        retained_table = _align_native_merge_table(existing_table.filter(pc.invert(chain_mask)), schema)
        combined_table = pa.concat_tables([retained_table, native_table])
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined_table = native_table

    sort_indices = pc.sort_indices(
        combined_table,
        sort_keys=[("chain", "ascending"), ("address", "ascending"), ("timestamp", "ascending")],
    )
    combined_table = combined_table.take(sort_indices)

    VaultHistoricalRead.write_uncleaned_arrow_table(combined_table, parquet_path)

    return len(combined_table)


def merge_native_protocols(
    merge_hypercore: bool = False,
    merge_grvt: bool = False,
    merge_lighter: bool = False,
    merge_hibachi: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    hyperliquid_hf_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
    hibachi_db_path: Path | None = None,
) -> dict[str, bool]:
    """Merge native protocol price data into the uncleaned parquet in one pass.

    Must run before cleaning so that native protocol data goes through
    the same cleaning pipeline as EVM vaults.

    For Hypercore, both the daily and HF DuckDB databases are always
    merged together so that switching between modes never loses
    historical data. All enabled native sources are collected before the
    existing parquet is read, then their chain partitions are replaced and
    the result is written once. This avoids repeatedly rewriting the much
    larger EVM data set.

    An unavailable, empty, or failed source leaves its existing chain
    partition untouched. This preserves the previous per-source failure
    semantics and prevents a transient database failure from deleting
    historical native-protocol prices.

    :param merge_hypercore: Merge Hyperliquid native (Hypercore) vault data
    :param merge_grvt: Merge GRVT native vault data
    :param merge_lighter: Merge Lighter native pool data
    :param merge_hibachi: Merge Hibachi native vault data
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the daily Hyperliquid DuckDB path
    :param hyperliquid_hf_db_path: Override for the HF Hyperliquid DuckDB path
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :param hibachi_db_path: Override for the Hibachi DuckDB path
    :return: Dictionary mapping step name to success boolean
    """
    parquet_path = uncleaned_parquet_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    steps: dict[str, bool] = {}
    replacements: dict[int, pd.DataFrame] = {}

    if merge_hypercore:
        try:
            logger.info("Merging Hypercore prices into uncleaned parquet")
            daily_path = hyperliquid_db_path or HYPERLIQUID_DAILY_METRICS_DATABASE
            hf_path = hyperliquid_hf_db_path or HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
            daily_db = None
            hf_db = None
            try:
                if daily_path.exists():
                    daily_db = HyperliquidDailyMetricsDatabase(daily_path)
                if hf_path.exists():
                    hf_db = HyperliquidHighFreqMetricsDatabase(hf_path)
                if daily_db is None and hf_db is None:
                    logger.warning("No Hyperliquid DuckDB databases found")
                    hypercore_df = pd.DataFrame()
                else:
                    hypercore_df = build_hypercore_prices_dataframe(daily_db=daily_db, hf_db=hf_db)
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

    if merge_grvt:
        try:
            logger.info("Merging GRVT prices into uncleaned parquet")
            g_db_path = grvt_db_path or GRVT_DAILY_METRICS_DATABASE
            db = GRVTDailyMetricsDatabase(g_db_path)
            try:
                grvt_df = build_grvt_prices_dataframe(db)
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

    if merge_lighter:
        try:
            logger.info("Merging Lighter prices into uncleaned parquet")
            l_db_path = lighter_db_path or LIGHTER_DAILY_METRICS_DATABASE
            db = LighterDailyMetricsDatabase(l_db_path)
            try:
                lighter_df = build_lighter_prices_dataframe(db)
            finally:
                db.close()
            if lighter_df.empty:
                logger.warning("No Lighter data to merge")
            else:
                replacements[LIGHTER_CHAIN_ID] = lighter_df
            logger.info("Lighter price merge: %d fresh Lighter price entries", len(lighter_df))
            steps["lighter-price-merge"] = True
        except Exception:
            logger.exception("Lighter price merge failed")
            steps["lighter-price-merge"] = False

    if merge_hibachi:
        try:
            logger.info("Merging Hibachi prices into uncleaned parquet")
            h_db_path = hibachi_db_path or HIBACHI_DAILY_METRICS_DATABASE
            db = HibachiDailyMetricsDatabase(h_db_path)
            try:
                hibachi_df = build_hibachi_prices_dataframe(db)
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

    if not replacements:
        return steps

    try:
        total_rows = _write_native_partitions_to_uncleaned_parquet(parquet_path, replacements)
        logger.info(
            "Merged %d native protocol chain partitions (%d fresh rows, %d total rows) into uncleaned %s in one PyArrow parquet write",
            len(replacements),
            sum(len(df) for df in replacements.values()),
            total_rows,
            parquet_path,
        )
    except Exception:
        logger.exception("Native protocol batch price merge failed")
        for step_name, chain_id in (
            ("hypercore-price-merge", HYPERCORE_CHAIN_ID),
            ("grvt-price-merge", GRVT_CHAIN_ID),
            ("lighter-price-merge", LIGHTER_CHAIN_ID),
            ("hibachi-price-merge", HIBACHI_CHAIN_ID),
        ):
            if chain_id in replacements:
                steps[step_name] = False

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
        generate_cleaned_vault_datasets(**kwargs)
        logger.info("Price cleaning complete")
        return True
    except OSError:
        # Corrupted parquet (e.g. "ZSTD decompression failed: Data
        # corruption detected") is a hard failure — the operator must
        # investigate and restore from backup.  Never swallow this.
        raise
    except Exception:
        logger.exception("Clean prices failed")
        return False


def export_sparklines() -> bool:
    """Export sparkline images to R2.

    :return: True if export succeeded
    """
    try:
        logger.info("Creating sparkline images")
        spec = importlib.util.spec_from_file_location("export_sparklines", "scripts/erc-4626/export-sparklines.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        logger.info("Sparkline export complete")
        return True
    except Exception:
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


def export_data_files() -> bool:
    """Export database files (parquet, pickle, DuckDB) to R2.

    :return: True if export succeeded
    """
    try:
        logger.info("Exporting data files")
        spec = importlib.util.spec_from_file_location("export_data_files", "scripts/erc-4626/export-data-files.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        logger.info("Data file export complete")
        return True
    except Exception:
        logger.exception("Export data files failed")
        return False


def export_sample_files(
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
        from eth_defi.vault.sample_export import export_sample_files_to_r2

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


def validate_top_vaults_config(skip_top_vaults: bool = False) -> None:
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


def export_top_vaults_json(
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
    primary (public) ``R2_TOP_VAULTS_*`` bucket and, if configured,
    also to the alternative (private) bucket via
    ``R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME``.

    This is a drop-in replacement for the standalone ``vault-analysis``
    docker image: the JSON generation and the R2 upload now both live
    inside the scanner post-processing pipeline.

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
        ``get_pipeline_data_dir() / "top_vaults_by_chain.json"``. The
        filename is intentionally kept identical to the existing public
        URL ``https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json``.

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

        # TODO: phase out public R2_TOP_VAULTS_BUCKET_NAME later once
        # downstream consumers (classification.py, add-vault-note skill,
        # deploy-lagoon-multichain.py) migrate to the private bucket.
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


def run_post_processing(
    scan_hypercore: bool = False,
    scan_grvt: bool = False,
    scan_lighter: bool = False,
    scan_hibachi: bool = False,
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
    vault_db_path: Path | None = None,
    cleaned_path: Path | None = None,
    settlement_db_path: Path | None = None,
    core3_db_path: Path | None = None,
    feed_db_path: Path | None = None,
) -> dict[str, bool]:
    """Run full post-processing pipeline after chain scans complete.

    Steps:
    1. Merge native protocol data into uncleaned parquet
    2. Clean prices
    3. Export top vaults JSON to R2
    4. Export sparklines to R2
    5. Export protocol metadata to R2
    6. Export data files (parquet, pickle) to R2
    7. Export Ethereum-only sample files to R2 (public bucket only)

    :param scan_hypercore: Whether to merge Hypercore data
    :param scan_grvt: Whether to merge GRVT data
    :param scan_lighter: Whether to merge Lighter data
    :param scan_hibachi: Whether to merge Hibachi data
    :param skip_cleaning: Skip price cleaning step
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
    :param vault_db_path: Override for the vault database pickle path
    :param cleaned_path: Override for the cleaned parquet output path
    :param settlement_db_path: Override for the vault settlement DuckDB path
    :param core3_db_path: Override for the Core3 risk intelligence DuckDB path
    :param feed_db_path: Override for the vault post feed DuckDB path (curator metadata and feed entries)
    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    # Step 1: Merge native protocols
    merge_results = merge_native_protocols(
        merge_hypercore=scan_hypercore,
        merge_grvt=scan_grvt,
        merge_lighter=scan_lighter,
        merge_hibachi=scan_hibachi,
        uncleaned_parquet_path=uncleaned_parquet_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=grvt_db_path,
        lighter_db_path=lighter_db_path,
        hibachi_db_path=hibachi_db_path,
    )
    steps.update(merge_results)

    # Step 2: Clean prices
    if skip_cleaning:
        logger.info("Skipping price cleaning (SKIP_CLEANING=true)")
    else:
        steps["clean-prices"] = clean_prices(
            vault_db_path=vault_db_path,
            uncleaned_path=uncleaned_parquet_path,
            cleaned_path=cleaned_path,
            settlement_db_path=settlement_db_path,
        )

    # Determine whether cleaned data is trustworthy for downstream exports.
    # If cleaning was explicitly skipped, the operator asserts the existing
    # cleaned parquet is valid.  If cleaning ran and failed, downstream steps
    # that depend on fresh cleaned data must NOT run — otherwise they would
    # silently re-upload stale artefacts, masking the failure.
    cleaning_ok = steps.get("clean-prices", True) if not skip_cleaning else True

    # Step 3: Export top vaults JSON (depends on cleaned parquet, must run before data-file upload)
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

    # Step 4: Export sparklines
    if skip_sparklines:
        logger.info("Skipping sparkline export (SKIP_SPARKLINES=true)")
    elif not cleaning_ok:
        logger.warning("Skipping sparkline export — clean_prices failed, refusing to export from stale data")
        steps["export-sparklines"] = False
    else:
        steps["export-sparklines"] = export_sparklines()

    # Step 5: Export protocol metadata (not derived from cleaned prices — always safe to run)
    if skip_metadata:
        logger.info("Skipping metadata export (SKIP_METADATA=true)")
    else:
        steps["export-protocol-metadata"] = export_protocol_metadata()

    # Step 6: Export data files
    if skip_data:
        logger.info("Skipping data file export (SKIP_DATA=true)")
    elif not cleaning_ok:
        logger.warning("Skipping data file export — clean_prices failed, refusing to export stale data")
        steps["export-data-files"] = False
    else:
        steps["export-data-files"] = export_data_files()

    # Step 7: Export Ethereum-only sample files (public bucket only)
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

    return steps
