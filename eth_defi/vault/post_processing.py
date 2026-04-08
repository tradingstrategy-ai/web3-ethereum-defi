"""Post-processing pipeline for vault price data.

Merges native protocol data (Hypercore, GRVT, Lighter) into the
uncleaned parquet, runs the cleaning pipeline, and optionally
uploads results to R2.

Used by both :py:mod:`scan-vaults-all-chains` and
:py:mod:`post-process-prices` scripts.
"""

import importlib.util
import logging
from pathlib import Path

from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault_data_export import merge_into_uncleaned_parquet as grvt_merge_parquet
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.vault_data_export import merge_into_uncleaned_parquet as hyperliquid_merge_parquet
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DAILY_METRICS_DATABASE
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.vault_data_export import merge_into_uncleaned_parquet as lighter_merge_parquet
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE

logger = logging.getLogger(__name__)


def merge_native_protocols(
    merge_hypercore: bool = False,
    merge_grvt: bool = False,
    merge_lighter: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
) -> dict[str, bool]:
    """Merge native protocol price data into the uncleaned parquet.

    Must run before cleaning so that native protocol data goes through
    the same cleaning pipeline as EVM vaults.

    :param merge_hypercore: Merge Hyperliquid native (Hypercore) vault data
    :param merge_grvt: Merge GRVT native vault data
    :param merge_lighter: Merge Lighter native pool data
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the Hyperliquid DuckDB path
    :param grvt_db_path: Override for the GRVT DuckDB path
    :param lighter_db_path: Override for the Lighter DuckDB path
    :return: Dictionary mapping step name to success boolean
    """
    parquet_path = uncleaned_parquet_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    steps = {}

    if merge_hypercore:
        try:
            logger.info("Merging Hypercore prices into uncleaned parquet")
            hl_db_path = hyperliquid_db_path or HYPERLIQUID_DAILY_METRICS_DATABASE
            db = HyperliquidDailyMetricsDatabase(hl_db_path)
            try:
                combined_df = hyperliquid_merge_parquet(db, parquet_path)
                hl_rows = len(combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("Hypercore price merge: %d Hyperliquid price entries in uncleaned parquet", hl_rows)
            finally:
                db.close()
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


def run_post_processing(
    scan_hypercore: bool = False,
    scan_grvt: bool = False,
    scan_lighter: bool = False,
    skip_cleaning: bool = False,
    skip_sparklines: bool = False,
    skip_metadata: bool = False,
    skip_data: bool = False,
    uncleaned_parquet_path: Path | None = None,
    hyperliquid_db_path: Path | None = None,
    grvt_db_path: Path | None = None,
    lighter_db_path: Path | None = None,
    vault_db_path: Path | None = None,
    cleaned_path: Path | None = None,
) -> dict[str, bool]:
    """Run full post-processing pipeline after chain scans complete.

    Steps:
    1. Merge native protocol data into uncleaned parquet
    2. Clean prices
    3. Export sparklines to R2
    4. Export protocol metadata to R2
    5. Export data files (parquet, pickle) to R2

    :param scan_hypercore: Whether to merge Hypercore data
    :param scan_grvt: Whether to merge GRVT data
    :param scan_lighter: Whether to merge Lighter data
    :param skip_cleaning: Skip price cleaning step
    :param skip_sparklines: Skip sparkline image export to R2
    :param skip_metadata: Skip protocol/stablecoin metadata export to R2
    :param skip_data: Skip data file (parquet, pickle) export to R2
    :param uncleaned_parquet_path: Override for the uncleaned parquet path
    :param hyperliquid_db_path: Override for the Hyperliquid DuckDB path
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

    # Step 3: Export sparklines
    if skip_sparklines:
        logger.info("Skipping sparkline export (SKIP_SPARKLINES=true)")
    else:
        steps["export-sparklines"] = export_sparklines()

    # Step 4: Export protocol metadata
    if skip_metadata:
        logger.info("Skipping metadata export (SKIP_METADATA=true)")
    else:
        steps["export-protocol-metadata"] = export_protocol_metadata()

    # Step 5: Export data files
    if skip_data:
        logger.info("Skipping data file export (SKIP_DATA=true)")
    else:
        steps["export-data-files"] = export_data_files()

    return steps
