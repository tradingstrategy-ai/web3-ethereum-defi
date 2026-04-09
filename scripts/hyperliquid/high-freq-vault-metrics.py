"""High-frequency Hyperliquid vault metrics pipeline.

Scans native Hyperliquid vaults at configurable sub-daily intervals
(default 4 h), computes share prices, stores metrics in a separate
HF DuckDB database, and merges the data into the existing ERC-4626
vault pipeline files (VaultDatabase pickle and cleaned Parquet).

Supports Webshare rotating proxies for parallel throughput when the
``WEBSHARE_API_KEY`` environment variable is set.

Usage:

.. code-block:: shell

    # Single run with defaults (4h interval, no proxies)
    poetry run python scripts/hyperliquid/high-freq-vault-metrics.py

    # Quick test with proxies and few vaults
    WEBSHARE_API_KEY=$WEBSHARE_API_KEY MIN_TVL=100000 MAX_VAULTS=5 \\
      poetry run python scripts/hyperliquid/high-freq-vault-metrics.py

    # Run with 1h interval and loop mode
    SCAN_INTERVAL=1h LOOP=1 \\
      poetry run python scripts/hyperliquid/high-freq-vault-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to HF DuckDB database. Default: ~/.tradingstrategy/vaults/hyperliquid-vaults-hf.duckdb
- ``SCAN_INTERVAL``: Scan interval (e.g. 1h, 4h, 6h). Default: 4h
- ``LOOP``: Set to ``1`` to run in loop mode (repeats every SCAN_INTERVAL). Default: single run.
- ``MIN_TVL``: Minimum TVL in USD. Default: 5000
- ``MAX_VAULTS``: Maximum vaults to process. Default: 20000
- ``MAX_WORKERS``: Parallel workers. Default: 16
- ``WEBSHARE_API_KEY``: Enables Webshare proxy rotation.
- ``WEBSHARE_PROXY_MODE``: Proxy type (backbone, residential). Default: backbone
- ``VAULT_ADDRESSES``: Comma-separated override vault list.
- ``REQUESTS_PER_SECOND``: Per-IP rate limit. Default: 1.0
- ``FLOW_BACKFILL_DAYS``: Days to backfill deposit/withdrawal flow. Default: 7
- ``VAULT_DB_PATH``: VaultDatabase pickle path.
- ``PARQUET_PATH``: Uncleaned parquet path.
"""

import datetime
import logging
import os
import time
from pathlib import Path

from eth_defi.hyperliquid.constants import (
    HYPERCORE_CHAIN_ID,
    HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE,
)
from eth_defi.hyperliquid.high_freq_metrics import run_high_freq_scan
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_data_export import (
    merge_into_vault_database,
    open_and_merge_hypercore_prices,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.scan_all_chains import parse_duration
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE

logger = logging.getLogger(__name__)


def main():
    # Configuration from environment
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/hyperliquid-hf-vault-metrics.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE

    scan_interval_str = os.environ.get("SCAN_INTERVAL", "4h")
    scan_interval = parse_duration(scan_interval_str)

    loop_mode = os.environ.get("LOOP", "").strip().lower() in ("1", "true", "yes")

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses = [a.strip() for a in vault_addresses_str.split(",") if a.strip()] or None

    min_tvl = float(os.environ.get("MIN_TVL", "5000"))
    max_vaults = int(os.environ.get("MAX_VAULTS", "20000"))
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))
    requests_per_second = float(os.environ.get("REQUESTS_PER_SECOND", "1.0"))
    flow_backfill_days = int(os.environ.get("FLOW_BACKFILL_DAYS", "7"))

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    # Load proxy rotator (None if WEBSHARE_API_KEY not set)
    rotator = None
    try:
        from eth_defi.event_reader.webshare import load_proxy_rotator

        rotator = load_proxy_rotator()
    except Exception as e:
        logger.info("Proxy rotator not available: %s", e)

    print("Hyperliquid high-frequency vault metrics pipeline")
    print(f"DuckDB path: {db_path}")
    print(f"Scan interval: {scan_interval}")
    print(f"Loop mode: {loop_mode}")
    print(f"Proxies: {'enabled' if rotator else 'disabled'}")
    if vault_addresses:
        print(f"Vault addresses: {', '.join(vault_addresses)}")
    else:
        print(f"Min TVL: ${min_tvl:,.0f}")
        print(f"Max vaults: {max_vaults}")
    print(f"Max workers: {max_workers}")
    print(f"Requests per second (per IP): {requests_per_second}")
    print(f"Flow backfill days: {flow_backfill_days}")

    # Create session with optional proxy support
    session = create_hyperliquid_session(
        requests_per_second=requests_per_second,
        rotator=rotator,
    )

    while True:
        cycle_start = time.time()

        # Step 1: Scan and store in DuckDB
        print(f"\nStep 1: Scanning Hyperliquid vaults (HF mode)...")
        db = run_high_freq_scan(
            session=session,
            db_path=db_path,
            scan_interval=scan_interval,
            min_tvl=min_tvl,
            max_vaults=max_vaults,
            max_workers=max_workers,
            vault_addresses=vault_addresses,
            flow_backfill_days=flow_backfill_days,
        )

        try:
            vault_count = db.get_vault_count()
            print(f"Stored HF metrics for {vault_count} vaults in DuckDB")

            # Step 2: Merge into VaultDatabase pickle
            print(f"\nStep 2: Merging into VaultDatabase at {vault_db_path}...")
            vault_db = merge_into_vault_database(db, vault_db_path)
            print(f"VaultDatabase now has {len(vault_db)} total vaults")

            # Step 3: Merge into uncleaned Parquet.
            # Uses the combined merge that reads both daily and HF databases
            # to avoid losing data when switching between modes.
            print(f"\nStep 3: Merging into uncleaned Parquet at {uncleaned_path}...")
            combined_df = open_and_merge_hypercore_prices(uncleaned_path, hf_db_path=db_path)
            hl_rows = combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID] if len(combined_df) > 0 else combined_df
            print(f"Uncleaned parquet now has {len(combined_df):,} total rows ({len(hl_rows):,} Hyperliquid)")

        finally:
            db.close()

        # Step 4: Run the cleaning pipeline
        print(f"\nStep 4: Running cleaning pipeline...")
        generate_cleaned_vault_datasets(
            vault_db_path=vault_db_path,
            price_df_path=uncleaned_path,
        )

        cycle_duration = time.time() - cycle_start
        print(f"\nCycle complete in {cycle_duration:.1f}s")

        if not loop_mode:
            break

        # Sleep until next cycle
        sleep_seconds = max(0, scan_interval.total_seconds() - cycle_duration)
        if sleep_seconds > 0:
            print(f"Sleeping {sleep_seconds:.0f}s until next cycle...")
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
