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

    # Custom retry count
    RETRY_COUNT=2 python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode - scan only specific chains (comma-separated)
    TEST_CHAINS=Berachain,Gnosis python scripts/erc-4626/scan-vaults-all-chains.py

    # Test mode without post-processing
    TEST_CHAINS=Berachain,Gnosis SKIP_POST_PROCESSING=true python scripts/erc-4626/scan-vaults-all-chains.py

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
    - RETRY_COUNT: Number of retry attempts (default: "1")
    - MAX_WORKERS: Number of parallel workers (default: "50")
    - FREQUENCY: "1h" or "1d" (default: "1h")
    - LOG_LEVEL: Logging level (default: "warning")
    - TEST_CHAINS: Comma-separated list of chain names to scan (default: all chains)
    - SKIP_POST_PROCESSING: "true" to skip post-processing steps (default: "false")
    - JSON_RPC_<CHAIN>: RPC URL for each chain (required per chain)
"""

import datetime
import logging
import os
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import (
    DEFAULT_READER_STATE_DATABASE,
    DEFAULT_UNCLEANED_PRICE_DATABASE,
    DEFAULT_VAULT_DATABASE,
)

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
        return False, {"error": str(e)}


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

            # Skip vaults with low activity
            if detection.deposit_count < min_deposit_threshold:
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
        return False, {"error": str(e)}


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
            if result.error:
                result.error += "; " + price_metrics.get("error", "Unknown error")
            else:
                result.error = price_metrics.get("error", "Unknown error")

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


def print_dashboard(results: dict[str, ChainResult]) -> None:
    """Print console dashboard showing scan progress.

    :param results: Dictionary mapping chain name to result
    """
    # Clear screen (simple approach)
    print("\n" * 3)

    print("=" * 100)
    print(" " * 35 + "Chain Scan Progress")
    print("=" * 100)
    print(f"{'Chain':<15} {'Status':<10} {'Vaults':<8} {'New':<6} {'Blocks':<22} {'Duration':<10} {'Retry':<5}")
    print("-" * 100)

    for result in results.values():
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

        print(f"{result.name:<15} {status:<10} {vaults:<8} {new:<6} {blocks:<22} {duration:<10} {retry:<5}")

    # Summary
    print("-" * 100)
    success_count = sum(1 for r in results.values() if r.status == "success")
    failed_count = sum(1 for r in results.values() if r.status == "failed")
    pending_count = sum(1 for r in results.values() if r.status == "pending")
    running_count = sum(1 for r in results.values() if r.status == "running")
    skipped_count = sum(1 for r in results.values() if r.status == "skipped")

    print(f"Summary: {success_count} success, {failed_count} failed, {pending_count} pending, {running_count} running, {skipped_count} skipped")
    print("=" * 100)


def run_post_processing() -> dict[str, bool]:
    """Run post-processing steps after all chain scans complete.

    :return: Dictionary mapping step name to success boolean
    """
    steps = {}

    # Step 1: Clean prices
    try:
        logger.info("Cleaning vault prices data")
        generate_cleaned_vault_datasets()
        steps["clean-prices"] = True
        logger.info("Price cleaning complete")
    except Exception as e:
        logger.exception("Clean prices failed")
        steps["clean-prices"] = False

    # Step 2: Export sparklines
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
    max_workers = int(os.environ.get("MAX_WORKERS", "50"))
    frequency = os.environ.get("FREQUENCY", "1h")
    skip_post_processing = os.environ.get("SKIP_POST_PROCESSING", "false").lower() == "true"

    # Test mode - filter chains if TEST_CHAINS is set
    test_chains_str = os.environ.get("TEST_CHAINS")
    if test_chains_str:
        test_chain_names = {name.strip() for name in test_chains_str.split(",")}
        logger.info("TEST MODE: Will only scan chains: %s", ", ".join(sorted(test_chain_names)))
    else:
        test_chain_names = None

    logger.info("=" * 80)
    logger.info("Starting multi-chain vault scan")
    logger.info("SCAN_PRICES: %s, RETRY_COUNT: %d, MAX_WORKERS: %d, FREQUENCY: %s", scan_prices, retry_count, max_workers, frequency)
    if skip_post_processing:
        logger.info("SKIP_POST_PROCESSING: true - post-processing will be skipped")
    if test_chain_names:
        logger.info("TEST_CHAINS: %s", ", ".join(sorted(test_chain_names)))
    logger.info("=" * 80)

    # Build chain configurations
    all_chains = build_chain_configs()

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

    # Display initial dashboard
    print_dashboard(results)

    # First pass - scan all chains
    logger.info("First pass: scanning %d chains", len(chains))
    for chain in chains:
        results[chain.name] = scan_chain(chain, scan_prices, max_workers, frequency, 0)

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

        print_dashboard(results)

    # Retry passes - retry failed chains
    for attempt in range(1, retry_count + 1):
        failed_chain_names = [name for name, r in results.items() if r.status == "failed"]
        if not failed_chain_names:
            logger.info("No failed chains to retry")
            break

        logger.info("Retry attempt %d: retrying %d failed chains", attempt, len(failed_chain_names))

        for chain_name in failed_chain_names:
            chain = next(c for c in chains if c.name == chain_name)
            result = scan_chain(chain, scan_prices, max_workers, frequency, attempt)
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

            print_dashboard(results)

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

        post_results = run_post_processing()
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
    logger.info("Scan complete at %s", datetime.datetime.utcnow().isoformat())

    # Exit with appropriate code
    if failed_count > 0:
        logger.warning("Exiting with error code due to %d failed chains", failed_count)
        sys.exit(1)
    else:
        logger.info("All scans completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
