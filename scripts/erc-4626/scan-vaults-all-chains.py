#!/usr/bin/env python3
"""Scan ERC-4626 vaults across all supported chains.

- Scan vaults and optionally prices for multiple chains
- Track success/failure status per chain
- Retry failed chains automatically
- Display live console dashboard
- Write detailed logs
- Run post-processing after all chains complete

Usage:

.. code-block:: shell

    # Scan all chains (vaults only, no prices)
    python scripts/erc-4626/scan-vaults-all-chains.py

    # Scan all chains with prices
    SCAN_PRICES=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include Hyperliquid native (Hypercore) vaults
    SCAN_HYPERCORE=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Include GRVT native vaults
    SCAN_GRVT=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Custom retry count
    RETRY_COUNT=2 python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode - scan only specific chains (comma-separated)
    TEST_CHAINS=Berachain,Gnosis python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode without post-processing
    TEST_CHAINS=Berachain,Gnosis SKIP_POST_PROCESSING=true python scripts/erc-4626/scan-vaults-all-chains.py

    # Disable specific chains (skip them)
    DISABLE_CHAINS=Plasma,Katana python scripts/erc-4626/scan-vaults-all-chains.py

Manual testing:

.. code-block:: shell

    # Test with Berachain and Gnosis (fast chains for testing)
    # Make sure you have set up .local-test.env with RPC URLs
    source .local-test.env && \
    TEST_CHAINS=Berachain,Gnosis \
    SKIP_POST_PROCESSING=true \
    MAX_WORKERS=20 \
    LOG_LEVEL=info \
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

    # Test with prices enabled
    source .local-test.env && \
    TEST_CHAINS=Berachain,Gnosis \
    SCAN_PRICES=true \
    SKIP_POST_PROCESSING=true \
    MAX_WORKERS=20 \
    LOG_LEVEL=info \
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

    # Test retry logic with intentionally bad RPC (will fail and retry)
    source .local-test.env && \
    TEST_CHAINS=Gnosis \
    RETRY_COUNT=2 \
    SKIP_POST_PROCESSING=true \
    JSON_RPC_GNOSIS=http://invalid-rpc-url \
    poetry run python scripts/erc-4626/scan-vaults-all-chains.py

Environment variables:
    - SCAN_PRICES: "true" or "false" (default: "false")
    - SCAN_HYPERCORE: "true" to scan Hyperliquid native (Hypercore) vaults via REST API (default: "false")
    - SCAN_GRVT: "true" to scan GRVT native vaults via public endpoints (default: "false")
    - RETRY_COUNT: Number of retry attempts (default: "1")
    - MAX_WORKERS: Number of parallel workers (default: "50")
    - FREQUENCY: "1h" or "1d" (default: "1h")
    - LOG_LEVEL: Logging level (default: "warning")
    - TEST_CHAINS: Comma-separated list of chain names to scan (default: all chains)
    - CHAIN_ORDER: Comma-separated list of chain names to scan in order (whitespace allowed, chains not listed are skipped)
    - DISABLE_CHAINS: Comma-separated list of chain names to skip (whitespace allowed)
    - SKIP_POST_PROCESSING: "true" to skip post-processing steps (default: "false")
    - JSON_RPC_<CHAIN>: RPC URL for each chain (required per chain)

Example CHAIN_ORDER for all chains:
    CHAIN_ORDER="Sonic, Monad, Hyperliquid, Base, Arbitrum, Ethereum, Linea, Gnosis, Zora, Polygon, Avalanche, Berachain, Unichain, Hemi, Plasma, Binance, Mantle, Katana, Ink, Blast, Soneium, Optimism"
"""

import datetime
import logging
import os
import pickle
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import verify_archive_node
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE

logger = logging.getLogger(__name__)


@dataclass
class ChainConfig:
    """Configuration for scanning a single chain"""

    #: Chain name (e.g., "Ethereum")
    name: str

    #: Environment variable name for RPC URL (e.g., "JSON_RPC_ETHEREUM")
    env_var: str

    #: Whether to scan vaults (False only for Unichain)
    scan_vaults: bool


@dataclass
class ChainResult:
    """Result of scanning a single chain"""

    #: Chain name
    name: str

    #: Status: "pending", "running", "success", "failed", "skipped"
    status: str

    #: Whether vault scan succeeded
    vault_scan_ok: bool | None = None

    #: Whether price scan succeeded
    price_scan_ok: bool | None = None

    #: First block scanned
    start_block: int | None = None

    #: Last block scanned
    end_block: int | None = None

    #: Total vault count
    vault_count: int | None = None

    #: Number of new vaults discovered
    new_vaults: int | None = None

    #: Number of price rows written
    price_rows: int | None = None

    #: Error message if failed
    error: str | None = None

    #: Full traceback string if failed
    traceback_str: str | None = None

    #: Scan duration in seconds
    duration: float | None = None

    #: Retry attempt number (0 for first attempt)
    retry_attempt: int = 0


def build_chain_configs() -> list[ChainConfig]:
    """Build list of chain configurations.

    Returns chains in the same order as scan-vaults-all-chains.sh
    """
    return [
        ChainConfig("Sonic", "JSON_RPC_SONIC", True),
        ChainConfig("Monad", "JSON_RPC_MONAD", True),
        ChainConfig("Hyperliquid", "JSON_RPC_HYPERLIQUID", True),
        ChainConfig("Base", "JSON_RPC_BASE", True),
        ChainConfig("Arbitrum", "JSON_RPC_ARBITRUM", True),
        ChainConfig("Ethereum", "JSON_RPC_ETHEREUM", True),
        ChainConfig("Linea", "JSON_RPC_LINEA", True),
        ChainConfig("Gnosis", "JSON_RPC_GNOSIS", True),
        ChainConfig("Zora", "JSON_RPC_ZORA", True),
        ChainConfig("Polygon", "JSON_RPC_POLYGON", True),
        ChainConfig("Avalanche", "JSON_RPC_AVALANCHE", True),
        ChainConfig("Berachain", "JSON_RPC_BERACHAIN", True),
        ChainConfig("Unichain", "JSON_RPC_UNICHAIN", False),  # Prices only
        ChainConfig("Hemi", "JSON_RPC_HEMI", True),
        ChainConfig("Plasma", "JSON_RPC_PLASMA", True),
        ChainConfig("Binance", "JSON_RPC_BINANCE", True),
        ChainConfig("Mantle", "JSON_RPC_MANTLE", True),
        ChainConfig("Katana", "JSON_RPC_KATANA", True),
        ChainConfig("Ink", "JSON_RPC_INK", True),
        ChainConfig("Blast", "JSON_RPC_BLAST", True),
        ChainConfig("Soneium", "JSON_RPC_SONEIUM", True),
        ChainConfig("Optimism", "JSON_RPC_OPTIMISM", True),
    ]


def scan_vaults_for_chain(rpc_url: str, max_workers: int) -> tuple[bool, dict]:
    """Scan vaults for a single chain by calling scan_leads() directly.

    :param rpc_url: RPC URL for the chain
    :param max_workers: Number of parallel workers
    :return: Tuple of (success, metrics_dict)
    """
    try:
        report = scan_leads(
            json_rpc_urls=rpc_url,
            vault_db_file=DEFAULT_VAULT_DATABASE,
            max_workers=max_workers,
            backend="auto",
            hypersync_api_key=os.environ.get("HYPERSYNC_API_KEY"),
            printer=lambda msg: None,  # Suppress output to keep logs clean
        )

        return True, {
            "start_block": report.start_block,
            "end_block": report.end_block,
            "vault_count": len(report.rows),
            "new_vaults": report.new_leads,
        }

    except Exception as e:
        logger.exception("Vault scan failed")
        return False, {"error": str(e), "traceback": traceback.format_exc()}


def scan_prices_for_chain(rpc_url: str, max_workers: int, frequency: str) -> tuple[bool, dict]:
    """Scan historical prices for a single chain.

    :param rpc_url: RPC URL for the chain
    :param max_workers: Number of parallel workers
    :param frequency: Scan frequency ("1h" or "1d")
    :return: Tuple of (success, metrics_dict)
    """
    try:
        # Setup Web3 connection
        web3 = create_multi_provider_web3(rpc_url)
        web3factory = MultiProviderWeb3Factory(rpc_url, retries=5)
        token_cache = TokenDiskCache()
        chain_id = web3.eth.chain_id

        # Load vault database
        if not DEFAULT_VAULT_DATABASE.exists():
            logger.warning("Vault database does not exist, skipping price scan")
            return True, {"rows_written": 0}

        vault_db = pickle.load(DEFAULT_VAULT_DATABASE.open("rb"))

        # Load reader states
        reader_states = {}
        if DEFAULT_READER_STATE_DATABASE.exists():
            reader_states = pickle.load(DEFAULT_READER_STATE_DATABASE.open("rb"))

        # Filter vaults for this chain
        chain_vaults = [v for v in vault_db.rows.values() if v["_detection_data"].chain == chain_id]

        if len(chain_vaults) == 0:
            logger.info("No vaults on chain %d, skipping price scan", chain_id)
            return True, {"rows_written": 0}

        # Create vault instances with filtering
        vaults = []
        min_deposit_threshold = 5

        for row in chain_vaults:
            detection = row["_detection_data"]

            # Skip vaults with low activity (but keep hardcoded protocol vaults)
            if detection.deposit_count < min_deposit_threshold and detection.address.lower() not in HARDCODED_PROTOCOLS:
                continue

            vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
            if vault:
                vault.first_seen_at_block = detection.first_seen_at_block
                vaults.append(vault)

        if len(vaults) == 0:
            logger.info("No vaults to scan on chain %d after filtering", chain_id)
            return True, {"rows_written": 0}

        # Configure HyperSync
        hypersync_config = configure_hypersync_from_env(web3)

        # Scan historical prices
        result = scan_historical_prices_to_parquet(
            output_fname=DEFAULT_UNCLEANED_PRICE_DATABASE,
            web3=web3,
            web3factory=web3factory,
            vaults=vaults,
            start_block=None,
            end_block=web3.eth.block_number,
            max_workers=max_workers,
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
        )

        # Save reader states
        if result["reader_states"]:
            pickle.dump(result["reader_states"], DEFAULT_READER_STATE_DATABASE.open("wb"))

        return True, {
            "rows_written": result["rows_written"],
            "start_block": result["start_block"],
            "end_block": result["end_block"],
        }

    except Exception as e:
        logger.exception("Price scan failed")
        return False, {"error": str(e), "traceback": traceback.format_exc()}


def scan_chain(config: ChainConfig, scan_prices: bool, max_workers: int, frequency: str, retry_attempt: int) -> ChainResult:
    """Scan a single chain (vaults and optionally prices).

    :param config: Chain configuration
    :param scan_prices: Whether to scan prices
    :param max_workers: Number of parallel workers
    :param frequency: Scan frequency
    :param retry_attempt: Retry attempt number (0 for first)
    :return: Scan result
    """
    result = ChainResult(name=config.name, status="running", retry_attempt=retry_attempt)

    # Check if RPC URL is configured
    rpc_url = os.environ.get(config.env_var)
    if not rpc_url:
        logger.warning("%s: SKIPPED - %s not configured", config.name, config.env_var)
        result.status = "skipped"
        result.error = f"{config.env_var} not set"
        return result

    logger.info("%s: Starting scan (retry %d)", config.name, retry_attempt)
    start_time = time.time()

    # Verify RPC providers and filter out broken ones
    try:
        rpc_url, latest_block = verify_archive_node(rpc_url, config.name)
        logger.info("%s: RPC archive node verification passed, latest block %s", config.name, f"{latest_block:,}")
    except RuntimeError as e:
        logger.error("%s: All archive node providers failed: %s", config.name, e)
        result.status = "failed"
        result.error = str(e)
        result.duration = time.time() - start_time
        return result

    # Scan vaults
    if config.scan_vaults:
        vault_success, vault_metrics = scan_vaults_for_chain(rpc_url, max_workers)
        result.vault_scan_ok = vault_success

        if vault_success:
            result.start_block = vault_metrics.get("start_block")
            result.end_block = vault_metrics.get("end_block")
            result.vault_count = vault_metrics.get("vault_count")
            result.new_vaults = vault_metrics.get("new_vaults")
        else:
            result.error = vault_metrics.get("error", "Unknown error")
            result.traceback_str = vault_metrics.get("traceback")

    # Scan prices
    if scan_prices:
        price_success, price_metrics = scan_prices_for_chain(rpc_url, max_workers, frequency)
        result.price_scan_ok = price_success

        if price_success:
            result.price_rows = price_metrics.get("rows_written")
            # Update block range if not set by vault scan
            if result.start_block is None:
                result.start_block = price_metrics.get("start_block")
            if result.end_block is None:
                result.end_block = price_metrics.get("end_block")
        else:
            price_error = price_metrics.get("error", "Unknown error")
            price_tb = price_metrics.get("traceback")
            if result.error:
                result.error += "; " + price_error
                if price_tb:
                    result.traceback_str = (result.traceback_str or "") + "\n" + price_tb
            else:
                result.error = price_error
                result.traceback_str = price_tb

    # Calculate duration
    result.duration = time.time() - start_time

    # Determine overall status
    vault_ok = result.vault_scan_ok if config.scan_vaults else True
    price_ok = result.price_scan_ok if scan_prices else True

    if vault_ok and price_ok:
        result.status = "success"
    else:
        result.status = "failed"

    return result


def scan_hypercore_fn(max_workers: int) -> ChainResult:
    """Scan Hyperliquid native (Hypercore) vaults via REST API.

    Runs the Hyperliquid daily metrics pipeline: fetches vault data,
    computes share prices, stores in DuckDB, and merges into the
    shared ERC-4626 pipeline files (VaultDatabase pickle + cleaned Parquet).

    :param max_workers:
        Number of parallel workers for fetching vault details.
    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
    from eth_defi.hyperliquid.daily_metrics import run_daily_scan
    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.vault_data_export import merge_into_vault_database

    result = ChainResult(name="Hypercore", status="running")
    start_time = time.time()

    try:
        session = create_hyperliquid_session(requests_per_second=2.75)

        db = run_daily_scan(
            session=session,
            db_path=HYPERLIQUID_DAILY_METRICS_DATABASE,
            max_workers=max_workers,
        )

        try:
            vault_count = db.get_vault_count()
            result.vault_count = vault_count
            result.vault_scan_ok = True

            merge_into_vault_database(db, DEFAULT_VAULT_DATABASE)
            # Price merge happens in post-processing after generate_cleaned_vault_datasets()
            # to avoid being overwritten by the EVM price cleaning step
            result.price_scan_ok = True
        finally:
            db.close()

        result.status = "success"

    except Exception as e:
        logger.exception("Hypercore scan failed")
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def scan_grvt_fn() -> ChainResult:
    """Scan GRVT native vaults via public endpoints.

    Runs the GRVT daily metrics pipeline: discovers vaults from the
    strategies page, fetches share price history from the public market
    data API, stores in DuckDB, and merges into the shared ERC-4626
    pipeline files (VaultDatabase pickle + cleaned Parquet).

    No authentication required.

    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.grvt.constants import GRVT_DAILY_METRICS_DATABASE
    from eth_defi.grvt.daily_metrics import run_daily_scan
    from eth_defi.grvt.vault_data_export import merge_into_vault_database

    result = ChainResult(name="GRVT", status="running")
    start_time = time.time()

    try:
        db = run_daily_scan(
            db_path=GRVT_DAILY_METRICS_DATABASE,
        )

        try:
            vault_count = db.get_vault_count()
            result.vault_count = vault_count
            result.vault_scan_ok = True

            merge_into_vault_database(db, DEFAULT_VAULT_DATABASE)
            # Price merge happens in post-processing after generate_cleaned_vault_datasets()
            # to avoid being overwritten by the EVM price cleaning step
            result.price_scan_ok = True
        finally:
            db.close()

        result.status = "success"

    except Exception as e:
        logger.exception("GRVT scan failed")
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def print_dashboard(results: dict[str, ChainResult], display_order: list[str] | None = None) -> None:
    """Print console dashboard showing scan progress.

    :param results: Dictionary mapping chain name to result
    :param display_order: Optional list of chain names specifying display order
    """
    # Clear screen (simple approach)
    print("\n" * 3)

    lines = []
    lines.append("=" * 100)
    lines.append(" " * 35 + "Chain Scan Progress")
    lines.append("=" * 100)
    lines.append(f"{'Chain':<15} {'Status':<10} {'Vaults':<8} {'New':<6} {'Blocks':<22} {'Duration':<10} {'Retry':<5}")
    lines.append("-" * 100)

    # Use display_order if provided, otherwise use dict order
    if display_order:
        ordered_results = [results[name] for name in display_order if name in results]
    else:
        ordered_results = list(results.values())

    for result in ordered_results:
        # Format fields
        status = result.status
        vaults = f"{result.vault_count:,}" if result.vault_count is not None else "-"
        new = f"{result.new_vaults}" if result.new_vaults is not None else "-"

        if result.start_block is not None and result.end_block is not None:
            blocks = f"{result.start_block:,} -> {result.end_block:,}"
        else:
            blocks = "-"

        duration = f"{result.duration:.1f}s" if result.duration is not None else "-"
        retry = str(result.retry_attempt)

        line = f"{result.name:<15} {status:<10} {vaults:<8} {new:<6} {blocks:<22} {duration:<10} {retry:<5}"
        if result.status == "failed" and result.error:
            # Truncate long error messages to fit the dashboard
            error_msg = result.error[:40]
            line += f"  {error_msg}"
        lines.append(line)

    # Summary
    lines.append("-" * 100)
    success_count = sum(1 for r in results.values() if r.status == "success")
    failed_count = sum(1 for r in results.values() if r.status == "failed")
    pending_count = sum(1 for r in results.values() if r.status == "pending")
    running_count = sum(1 for r in results.values() if r.status == "running")
    skipped_count = sum(1 for r in results.values() if r.status == "skipped")

    lines.append(f"Summary: {success_count} success, {failed_count} failed, {pending_count} pending, {running_count} running, {skipped_count} skipped")
    lines.append("=" * 100)

    # Print to console and log at info level
    dashboard = "\n".join(lines)
    print(dashboard)
    logger.info(dashboard)

    # Print full error messages below the dashboard
    failed_results = [r for r in ordered_results if r.status == "failed" and r.error]
    for r in failed_results:
        logger.error("%s: %s", r.name, r.error)


def run_post_processing(scan_hypercore: bool = False, scan_grvt: bool = False) -> dict[str, bool]:
    """Run post-processing steps after all chain scans complete.

    :param scan_hypercore:
        Whether to merge Hypercore (Hyperliquid native) price data
        into the uncleaned Parquet before the cleaning step, so
        Hypercore data goes through the same cleaning pipeline.
    :param scan_grvt:
        Whether to merge GRVT native vault price data into the
        uncleaned Parquet before the cleaning step.
    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    # Step 1: Merge Hypercore prices into the uncleaned Parquet
    # Must run BEFORE clean-prices so Hypercore data goes through the
    # same cleaning pipeline as EVM vaults.
    if scan_hypercore:
        try:
            from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
            from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
            from eth_defi.hyperliquid.vault_data_export import merge_into_uncleaned_parquet
            from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE

            logger.info("Merging Hypercore prices into uncleaned Parquet")
            db = HyperliquidDailyMetricsDatabase(HYPERLIQUID_DAILY_METRICS_DATABASE)
            try:
                combined_df = merge_into_uncleaned_parquet(db, DEFAULT_UNCLEANED_PRICE_DATABASE)
                hl_rows = len(combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("Hypercore price merge: %d Hyperliquid price entries in uncleaned Parquet", hl_rows)
            finally:
                db.close()
            steps["hypercore-price-merge"] = True
            logger.info("Hypercore price merge complete")
        except Exception as e:
            logger.exception("Hypercore price merge failed")
            steps["hypercore-price-merge"] = False

    # Step 1b: Merge GRVT prices into the uncleaned Parquet
    # Must run BEFORE clean-prices so GRVT data goes through the
    # same cleaning pipeline as EVM vaults.
    if scan_grvt:
        try:
            from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
            from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
            from eth_defi.grvt.vault_data_export import merge_into_uncleaned_parquet as grvt_merge_parquet
            from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE

            logger.info("Merging GRVT prices into uncleaned Parquet")
            db = GRVTDailyMetricsDatabase(GRVT_DAILY_METRICS_DATABASE)
            try:
                combined_df = grvt_merge_parquet(db, DEFAULT_UNCLEANED_PRICE_DATABASE)
                grvt_rows = len(combined_df[combined_df["chain"] == GRVT_CHAIN_ID]) if len(combined_df) > 0 else 0
                logger.info("GRVT price merge: %d GRVT price entries in uncleaned Parquet", grvt_rows)
            finally:
                db.close()
            steps["grvt-price-merge"] = True
            logger.info("GRVT price merge complete")
        except Exception as e:
            logger.exception("GRVT price merge failed")
            steps["grvt-price-merge"] = False

    # Step 2: Clean prices (all vaults — reads raw parquet including Hypercore + GRVT, writes cleaned)
    try:
        logger.info("Cleaning vault prices data")
        generate_cleaned_vault_datasets()
        steps["clean-prices"] = True
        logger.info("Price cleaning complete")
    except Exception as e:
        logger.exception("Clean prices failed")
        steps["clean-prices"] = False

    # Step 3: Export sparklines
    try:
        logger.info("Creating sparkline images")
        import importlib.util

        spec = importlib.util.spec_from_file_location("export_sparklines", "scripts/erc-4626/export-sparklines.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        steps["export-sparklines"] = True
        logger.info("Sparkline export complete")
    except Exception as e:
        logger.exception("Export sparklines failed")
        steps["export-sparklines"] = False

    # Step 3: Export protocol metadata
    try:
        logger.info("Exporting protocol metadata files")
        spec = importlib.util.spec_from_file_location("export_protocol_metadata", "scripts/erc-4626/export-protocol-metadata.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        steps["export-protocol-metadata"] = True
        logger.info("Protocol metadata export complete")
    except Exception as e:
        logger.exception("Export protocol metadata failed")
        steps["export-protocol-metadata"] = False

    return steps


def main():
    """Main execution function"""
    # Setup logging
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "warning"),
        log_file=Path("logs/scan-all-chains.log"),
        clear_log_file=True,
    )

    # Read configuration from environment
    retry_count = int(os.environ.get("RETRY_COUNT", "1"))
    scan_prices = os.environ.get("SCAN_PRICES", "false").lower() == "true"
    scan_hypercore = os.environ.get("SCAN_HYPERCORE", "false").lower() == "true"
    scan_grvt = os.environ.get("SCAN_GRVT", "false").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "50"))
    frequency = os.environ.get("FREQUENCY", "1h")
    skip_post_processing = os.environ.get("SKIP_POST_PROCESSING", "false").lower() == "true"

    # Test mode - filter chains if TEST_CHAINS is set
    disable_chains_str = os.environ.get("DISABLE_CHAINS")
    test_chains_str = os.environ.get("TEST_CHAINS")
    if test_chains_str:
        test_chain_names = {name.strip() for name in test_chains_str.split(",")}
        logger.info("TEST MODE: Will only scan chains: %s", ", ".join(sorted(test_chain_names)))
    else:
        test_chain_names = None

    logger.info("=" * 80)
    logger.info("Starting multi-chain vault scan")
    logger.info("SCAN_PRICES: %s, SCAN_HYPERCORE: %s, SCAN_GRVT: %s, RETRY_COUNT: %d, MAX_WORKERS: %d, FREQUENCY: %s", scan_prices, scan_hypercore, scan_grvt, retry_count, max_workers, frequency)
    if skip_post_processing:
        logger.info("SKIP_POST_PROCESSING: true - post-processing will be skipped")
    if test_chain_names:
        logger.info("TEST_CHAINS: %s", ", ".join(sorted(test_chain_names)))
    if disable_chains_str:
        logger.info("DISABLE_CHAINS: %s", disable_chains_str)
    logger.info("=" * 80)

    # Build chain configurations
    all_chains = build_chain_configs()
    chain_by_name = {c.name: c for c in all_chains}

    # Reorder and filter chains if CHAIN_ORDER is set
    chain_order_str = os.environ.get("CHAIN_ORDER")
    skipped_by_order = []  # Chains not in CHAIN_ORDER
    if chain_order_str:
        chain_order = [name.strip() for name in chain_order_str.split(",")]
        reordered_chains = []
        for name in chain_order:
            if name in chain_by_name:
                reordered_chains.append(chain_by_name[name])
            else:
                logger.warning("Unknown chain in CHAIN_ORDER: %s", name)
        # Track chains not in CHAIN_ORDER as skipped
        specified_names = set(chain_order)
        for chain in all_chains:
            if chain.name not in specified_names:
                skipped_by_order.append(chain)
        all_chains = reordered_chains
        logger.info("CHAIN_ORDER: %s", ", ".join([c.name for c in all_chains]))
        if skipped_by_order:
            logger.info("Chains not in CHAIN_ORDER (will be skipped): %s", ", ".join([c.name for c in skipped_by_order]))

    # Disable specific chains if DISABLE_CHAINS is set
    disabled_chains = []
    if disable_chains_str:
        disable_chain_names = {name.strip() for name in disable_chains_str.split(",")}
        disabled_chains = [c for c in all_chains if c.name in disable_chain_names]
        all_chains = [c for c in all_chains if c.name not in disable_chain_names]
        logger.info("DISABLE_CHAINS: %s", ", ".join(sorted(disable_chain_names)))

    # Filter chains if in test mode
    if test_chain_names:
        chains = [c for c in all_chains if c.name in test_chain_names]
        if len(chains) == 0:
            logger.error("No matching chains found for TEST_CHAINS=%s", test_chains_str)
            logger.error("Available chains: %s", ", ".join([c.name for c in all_chains]))
            sys.exit(1)
        if len(chains) < len(test_chain_names):
            found_names = {c.name for c in chains}
            missing_names = test_chain_names - found_names
            logger.warning("Some test chains not found: %s", ", ".join(missing_names))
    else:
        chains = all_chains

    results = {c.name: ChainResult(name=c.name, status="pending", retry_attempt=0) for c in chains}

    # Add Hypercore (Hyperliquid native vaults) to tracking
    if scan_hypercore:
        results["Hypercore"] = ChainResult(name="Hypercore", status="pending")

    # Add GRVT (native vaults) to tracking
    if scan_grvt:
        results["GRVT"] = ChainResult(name="GRVT", status="pending")

    # Add chains skipped by CHAIN_ORDER to results
    for chain in skipped_by_order:
        results[chain.name] = ChainResult(name=chain.name, status="skipped", error="Not in CHAIN_ORDER")

    # Add disabled chains to results
    for chain in disabled_chains:
        results[chain.name] = ChainResult(name=chain.name, status="skipped", error="Disabled via DISABLE_CHAINS")

    # Build display order: EVM chains first, then Hypercore/GRVT, then skipped/disabled
    display_order = [c.name for c in chains]
    if scan_hypercore:
        display_order.append("Hypercore")
    if scan_grvt:
        display_order.append("GRVT")
    display_order += [c.name for c in skipped_by_order] + [c.name for c in disabled_chains]

    # Display initial dashboard
    print_dashboard(results, display_order)

    # First pass - scan all chains
    logger.info("First pass: scanning %d chains", len(chains))
    for chain in chains:
        try:
            results[chain.name] = scan_chain(chain, scan_prices, max_workers, frequency, 0)
        except Exception as e:
            logger.exception("Chain %s crashed with unhandled exception", chain.name)
            results[chain.name] = ChainResult(
                name=chain.name,
                status="failed",
                error=str(e),
                traceback_str=traceback.format_exc(),
                retry_attempt=0,
            )

        # Log result
        r = results[chain.name]
        if r.status == "success":
            logger.info(
                "%s: SUCCESS - blocks %s-%s, %d vaults (%d new), %d price rows",
                chain.name,
                r.start_block or "?",
                r.end_block or "?",
                r.vault_count or 0,
                r.new_vaults or 0,
                r.price_rows or 0,
            )
        elif r.status == "failed":
            logger.error("%s: FAILED - %s", chain.name, r.error)
        elif r.status == "skipped":
            logger.warning("%s: SKIPPED - %s", chain.name, r.error)

        print_dashboard(results, display_order)

    # Hypercore scan (Hyperliquid native vaults via REST API)
    if scan_hypercore:
        logger.info("Scanning Hypercore (Hyperliquid native vaults)")
        try:
            results["Hypercore"] = scan_hypercore_fn(max_workers)
        except Exception as e:
            logger.exception("Hypercore scan crashed with unhandled exception")
            results["Hypercore"] = ChainResult(
                name="Hypercore",
                status="failed",
                error=str(e),
                traceback_str=traceback.format_exc(),
            )

        r = results["Hypercore"]
        if r.status == "success":
            logger.info("Hypercore: SUCCESS - %d vaults", r.vault_count or 0)
        elif r.status == "failed":
            logger.error("Hypercore: FAILED - %s", r.error)

        print_dashboard(results, display_order)

    # GRVT scan (native vaults via public endpoints)
    if scan_grvt:
        logger.info("Scanning GRVT (native vaults)")
        try:
            results["GRVT"] = scan_grvt_fn()
        except Exception as e:
            logger.exception("GRVT scan crashed with unhandled exception")
            results["GRVT"] = ChainResult(
                name="GRVT",
                status="failed",
                error=str(e),
                traceback_str=traceback.format_exc(),
            )

        r = results["GRVT"]
        if r.status == "success":
            logger.info("GRVT: SUCCESS - %d vaults", r.vault_count or 0)
        elif r.status == "failed":
            logger.error("GRVT: FAILED - %s", r.error)

        print_dashboard(results, display_order)

    # Retry passes - retry failed EVM chains (Hypercore/GRVT are not retried)
    evm_chain_names = {c.name for c in chains}
    for attempt in range(1, retry_count + 1):
        failed_chain_names = [name for name, r in results.items() if r.status == "failed" and name in evm_chain_names]
        if not failed_chain_names:
            logger.info("No failed chains to retry")
            break

        logger.info("Retry attempt %d: retrying %d failed chains", attempt, len(failed_chain_names))

        for chain_name in failed_chain_names:
            chain = next(c for c in chains if c.name == chain_name)
            try:
                result = scan_chain(chain, scan_prices, max_workers, frequency, attempt)
            except Exception as e:
                logger.exception("Chain %s crashed with unhandled exception (retry %d)", chain.name, attempt)
                result = ChainResult(
                    name=chain.name,
                    status="failed",
                    error=str(e),
                    traceback_str=traceback.format_exc(),
                    retry_attempt=attempt,
                )
            results[chain.name] = result

            # Log result
            if result.status == "success":
                logger.info(
                    "%s (retry %d): SUCCESS - blocks %s-%s, %d vaults (%d new)",
                    chain.name,
                    attempt,
                    result.start_block or "?",
                    result.end_block or "?",
                    result.vault_count or 0,
                    result.new_vaults or 0,
                )
            else:
                logger.error("%s (retry %d): FAILED - %s", chain.name, attempt, result.error)

            print_dashboard(results, display_order)

    # Post-processing
    if skip_post_processing:
        logger.info("=" * 80)
        logger.info("Skipping post-processing (SKIP_POST_PROCESSING=true)")
        logger.info("=" * 80)
        post_results = {}
    else:
        logger.info("=" * 80)
        logger.info("All chain scans complete, starting post-processing")
        logger.info("=" * 80)

        post_results = run_post_processing(scan_hypercore=scan_hypercore, scan_grvt=scan_grvt)
        for step, success in post_results.items():
            status_str = "SUCCESS" if success else "FAILED"
            logger.info("Post-processing %s: %s", step, status_str)

    # Final summary
    success_count = sum(1 for r in results.values() if r.status == "success")
    failed_count = sum(1 for r in results.values() if r.status == "failed")
    skipped_count = sum(1 for r in results.values() if r.status == "skipped")

    logger.info("=" * 80)
    logger.info("Final summary: %d success, %d failed, %d skipped", success_count, failed_count, skipped_count)

    if failed_count > 0:
        logger.warning("Failed chains:")
        for name, r in results.items():
            if r.status == "failed":
                logger.warning("  - %s: %s", name, r.error)

    logger.info("=" * 80)
    logger.info("Scan complete at %s", datetime.datetime.now(datetime.timezone.utc).isoformat())

    # Print full tracebacks for all failed chains before the final dashboard
    failed_results = [r for r in results.values() if r.status == "failed" and r.traceback_str]
    if failed_results:
        print("\n")
        print("=" * 100)
        print(" " * 30 + "Full tracebacks for failed chains")
        print("=" * 100)
        for r in failed_results:
            print(f"\n--- {r.name} (retry {r.retry_attempt}) ---")
            print(r.traceback_str)
        print("=" * 100)

    # Print final dashboard
    print_dashboard(results, display_order)

    # Exit with appropriate code
    # Only exit with error if there are no successful chains at all
    if success_count == 0 and failed_count > 0:
        logger.warning("Exiting with error code - no chains succeeded (%d failed)", failed_count)
        sys.exit(1)
    elif failed_count > 0:
        logger.warning("%d chains failed but %d succeeded - exiting with success", failed_count, success_count)
        sys.exit(0)
    else:
        logger.info("All scans completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
