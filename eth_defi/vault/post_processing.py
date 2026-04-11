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

from eth_defi.cloudflare_r2 import create_r2_client, upload_file_to_r2
from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault_data_export import merge_into_uncleaned_parquet as grvt_merge_parquet
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.vault_data_export import open_and_merge_hypercore_prices
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DAILY_METRICS_DATABASE
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.vault_data_export import merge_into_uncleaned_parquet as lighter_merge_parquet
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
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


def merge_native_protocols(
    merge_hypercore: bool = False,
    merge_grvt: bool = False,
    merge_lighter: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    hyperliquid_hf_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
) -> dict[str, bool]:
    """Merge native protocol price data into the uncleaned parquet.

    Must run before cleaning so that native protocol data goes through
    the same cleaning pipeline as EVM vaults.

    For Hypercore, both the daily and HF DuckDB databases are always
    merged together so that switching between modes never loses
    historical data.

    :param merge_hypercore: Merge Hyperliquid native (Hypercore) vault data
    :param merge_grvt: Merge GRVT native vault data
    :param merge_lighter: Merge Lighter native pool data
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the daily Hyperliquid DuckDB path
    :param hyperliquid_hf_db_path: Override for the HF Hyperliquid DuckDB path
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :return: Dictionary mapping step name to success boolean
    """
    parquet_path = uncleaned_parquet_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    steps = {}

    if merge_hypercore:
        try:
            logger.info("Merging Hypercore prices into uncleaned parquet")
            combined_df = open_and_merge_hypercore_prices(
                parquet_path,
                daily_db_path=hyperliquid_db_path,
                hf_db_path=hyperliquid_hf_db_path,
            )
            hl_rows = len(combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID]) if len(combined_df) > 0 else 0
            logger.info("Hypercore price merge: %d Hyperliquid price entries in uncleaned parquet", hl_rows)
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
                combined_df = grvt_merge_parquet(db, parquet_path)
                grvt_rows = len(combined_df[combined_df["chain"] == GRVT_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("GRVT price merge: %d GRVT price entries in uncleaned parquet", grvt_rows)
            finally:
                db.close()
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
                combined_df = lighter_merge_parquet(db, parquet_path)
                lighter_rows = len(combined_df[combined_df["chain"] == LIGHTER_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("Lighter price merge: %d Lighter price entries in uncleaned parquet", lighter_rows)
            finally:
                db.close()
            steps["lighter-price-merge"] = True
        except Exception:
            logger.exception("Lighter price merge failed")
            steps["lighter-price-merge"] = False

    return steps


def clean_prices(
    vault_db_path: Path | None = None,
    uncleaned_path: Path | None = None,
    cleaned_path: Path | None = None,
) -> bool:
    """Run the price cleaning pipeline.

    Reads uncleaned parquet and writes cleaned parquet.

    :param vault_db_path: Override for the vault database pickle path
    :param uncleaned_path: Override for the uncleaned parquet path
    :param cleaned_path: Override for the cleaned parquet output path
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
        generate_cleaned_vault_datasets(**kwargs)
        logger.info("Price cleaning complete")
        return True
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
    """Export database files (parquet, pickle) to R2.

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
) -> bool:
    """Generate the top-vaults lifetime-metrics JSON and upload to R2.

    Runs :py:mod:`scripts/erc-4626/vault-analysis-json` against the
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

    :return:
        ``True`` if the JSON was generated and uploaded, ``False`` on
        any failure. Matches the behaviour of the other ``export_*``
        helpers so the caller can log and continue.
    """
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
        spec = importlib.util.spec_from_file_location("vault_analysis_json", "scripts/erc-4626/vault-analysis-json.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main(
            data_dir=base,
            vault_db_path=vault_db_path,
            parquet_path=cleaned_path,
            output_path=output_path,
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

        # Primary upload — the public bucket that serves
        # https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json
        uploaded = upload_file_to_r2(
            s3_client=s3_client,
            file_path=output_path,
            bucket_name=bucket_name,
            object_name=object_key,
            skip_if_current=True,
        )
        if uploaded:
            logger.info("Uploaded %s to s3://%s/%s", output_path, bucket_name, object_key)
            if public_url:
                logger.info("  -> %s/%s", public_url.rstrip("/"), object_key)
        else:
            logger.info("Skipped unchanged %s for s3://%s/%s", output_path, bucket_name, object_key)

        # TODO: phase out public R2_TOP_VAULTS_BUCKET_NAME later once
        # downstream consumers (classification.py, add-vault-note skill,
        # deploy-lagoon-multichain.py) migrate to the private bucket.
        alt_bucket_name = os.environ.get("R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME")
        if alt_bucket_name:
            alt_uploaded = upload_file_to_r2(
                s3_client=s3_client,
                file_path=output_path,
                bucket_name=alt_bucket_name,
                object_name=object_key,
                skip_if_current=True,
            )
            if alt_uploaded:
                logger.info("Uploaded %s to alternative s3://%s/%s", output_path, alt_bucket_name, object_key)
            else:
                logger.info("Skipped unchanged %s for alternative s3://%s/%s", output_path, alt_bucket_name, object_key)

        logger.info("Top vaults JSON export complete")
        return True
    except Exception:
        logger.exception("Export top vaults JSON failed")
        return False


def run_post_processing(
    scan_hypercore: bool = False,
    scan_grvt: bool = False,
    scan_lighter: bool = False,
    skip_cleaning: bool = False,
    skip_top_vaults: bool = False,
    skip_sparklines: bool = False,
    skip_metadata: bool = False,
    skip_data: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    hyperliquid_hf_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
    vault_db_path: Path | None = None,
    cleaned_path: Path | None = None,
) -> dict[str, bool]:
    """Run full post-processing pipeline after chain scans complete.

    Steps:
    1. Merge native protocol data into uncleaned parquet
    2. Clean prices
    3. Export top vaults JSON to R2
    4. Export sparklines to R2
    5. Export protocol metadata to R2
    6. Export data files (parquet, pickle) to R2

    :param scan_hypercore: Whether to merge Hypercore data
    :param scan_grvt: Whether to merge GRVT data
    :param scan_lighter: Whether to merge Lighter data
    :param skip_cleaning: Skip price cleaning step
    :param skip_top_vaults: Skip top-vaults JSON generation and R2 upload
    :param skip_sparklines: Skip sparkline image export to R2
    :param skip_metadata: Skip protocol/stablecoin metadata export to R2
    :param skip_data: Skip data file (parquet, pickle) export to R2
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the daily Hyperliquid DuckDB path
    :param hyperliquid_hf_db_path: Override for the HF Hyperliquid DuckDB path
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :param vault_db_path: Override for the vault database pickle path
    :param cleaned_path: Override for the cleaned parquet output path
    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    # Step 1: Merge native protocols
    merge_results = merge_native_protocols(
        merge_hypercore=scan_hypercore,
        merge_grvt=scan_grvt,
        merge_lighter=scan_lighter,
        uncleaned_parquet_path=uncleaned_parquet_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=grvt_db_path,
        lighter_db_path=lighter_db_path,
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
        )

    # Step 3: Export top vaults JSON (depends on cleaned parquet, must run before data-file upload)
    if skip_top_vaults:
        logger.info("Skipping top vaults export (SKIP_TOP_VAULTS=true)")
    else:
        steps["export-top-vaults-json"] = export_top_vaults_json(
            vault_db_path=vault_db_path,
            cleaned_path=cleaned_path,
        )

    # Step 4: Export sparklines
    if skip_sparklines:
        logger.info("Skipping sparkline export (SKIP_SPARKLINES=true)")
    else:
        steps["export-sparklines"] = export_sparklines()

    # Step 5: Export protocol metadata
    if skip_metadata:
        logger.info("Skipping metadata export (SKIP_METADATA=true)")
    else:
        steps["export-protocol-metadata"] = export_protocol_metadata()

    # Step 6: Export data files
    if skip_data:
        logger.info("Skipping data file export (SKIP_DATA=true)")
    else:
        steps["export-data-files"] = export_data_files()

    return steps
