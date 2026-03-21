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
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


def merge_native_protocols(
    merge_hypercore: bool = False,
    merge_grvt: bool = False,
    merge_lighter: bool = False,
) -> dict[str, bool]:
    """Merge native protocol price data into the uncleaned parquet.

    Must run before cleaning so that native protocol data goes through
    the same cleaning pipeline as EVM vaults.

    :param merge_hypercore: Merge Hyperliquid native (Hypercore) vault data
    :param merge_grvt: Merge GRVT native vault data
    :param merge_lighter: Merge Lighter native pool data
    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    if merge_hypercore:
        try:
            logger.info("Merging Hypercore prices into uncleaned parquet")
            db = HyperliquidDailyMetricsDatabase(HYPERLIQUID_DAILY_METRICS_DATABASE)
            try:
                combined_df = hyperliquid_merge_parquet(db, DEFAULT_UNCLEANED_PRICE_DATABASE)
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
            db = GRVTDailyMetricsDatabase(GRVT_DAILY_METRICS_DATABASE)
            try:
                combined_df = grvt_merge_parquet(db, DEFAULT_UNCLEANED_PRICE_DATABASE)
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
            db = LighterDailyMetricsDatabase(LIGHTER_DAILY_METRICS_DATABASE)
            try:
                combined_df = lighter_merge_parquet(db, DEFAULT_UNCLEANED_PRICE_DATABASE)
                lighter_rows = len(combined_df[combined_df["chain"] == LIGHTER_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("Lighter price merge: %d Lighter price entries in uncleaned parquet", lighter_rows)
            finally:
                db.close()
            steps["lighter-price-merge"] = True
        except Exception:
            logger.exception("Lighter price merge failed")
            steps["lighter-price-merge"] = False

    return steps


def clean_prices() -> bool:
    """Run the price cleaning pipeline.

    Reads uncleaned parquet and writes cleaned parquet.

    :return: True if cleaning succeeded
    """
    try:
        logger.info("Cleaning vault prices data")
        generate_cleaned_vault_datasets()
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
    """Export protocol metadata and database files to R2.

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


def run_post_processing(
    scan_hypercore: bool = False,
    scan_grvt: bool = False,
    scan_lighter: bool = False,
) -> dict[str, bool]:
    """Run full post-processing pipeline after chain scans complete.

    Steps:
    1. Merge native protocol data into uncleaned parquet
    2. Clean prices
    3. Export sparklines
    4. Export protocol metadata and database files to R2

    :param scan_hypercore: Whether to merge Hypercore data
    :param scan_grvt: Whether to merge GRVT data
    :param scan_lighter: Whether to merge Lighter data
    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    # Step 1: Merge native protocols
    merge_results = merge_native_protocols(
        merge_hypercore=scan_hypercore,
        merge_grvt=scan_grvt,
        merge_lighter=scan_lighter,
    )
    steps.update(merge_results)

    # Step 2: Clean prices
    steps["clean-prices"] = clean_prices()

    # Step 3: Export sparklines
    steps["export-sparklines"] = export_sparklines()

    # Step 4: Export protocol metadata
    steps["export-protocol-metadata"] = export_protocol_metadata()

    return steps
