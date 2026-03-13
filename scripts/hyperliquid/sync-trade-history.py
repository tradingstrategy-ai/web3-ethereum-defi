"""Sync trade history for whitelisted Hyperliquid accounts.

Fetches fills, funding payments, and ledger events for whitelisted accounts
and stores them in a DuckDB database. Supports incremental sync that
accumulates data beyond the 10K fill API limit.

Usage:

.. code-block:: shell

    # Sync specific addresses
    ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e,0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # Auto-discover vaults with peak TVL >= $100k
    MIN_VAULT_PEAK_TVL=100000 \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # Non-interactive mode (skip confirmation prompt)
    MIN_VAULT_PEAK_TVL=100000 INTERACTIVE=false \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # With debug logging
    LOG_LEVEL=info ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
      poetry run python scripts/hyperliquid/sync-trade-history.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``TRADE_HISTORY_DB_PATH``: DuckDB path. Default: ~/.tradingstrategy/hyperliquid/trade-history.duckdb
- ``ADDRESSES``: Comma-separated addresses to add to whitelist and sync.
  If not set, syncs all existing whitelisted accounts.
- ``LABELS``: Comma-separated labels matching ADDRESSES (optional).
- ``MIN_VAULT_PEAK_TVL``: Auto-discover vaults with peak TVL >= this value (USD).
  Reads from cleaned vault prices Parquet. Mutually exclusive with ADDRESSES.
- ``PARQUET_PATH``: Path to cleaned vault prices Parquet (for MIN_VAULT_PEAK_TVL).
- ``INTERACTIVE``: Set to ``false`` to skip confirmation prompts (for CI/cron). Default: true.
- ``MAX_WORKERS``: Parallel threads (default: 1, DuckDB single-writer).
- ``WEBSHARE_API_KEY``: Webshare proxy API token. When set, each worker gets
  its own proxy for API requests, with automatic rotation on failure.
- ``WEBSHARE_PROXY_MODE``: Proxy mode — "backbone" (residential/server, default) or "direct" (datacenter).
"""

import logging
import os
import sys
from pathlib import Path

from tabulate import tabulate

from eth_defi.event_reader.webshare import load_proxy_rotator, print_proxy_dashboard
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_filter import fetch_vaults_by_peak_tvl
from eth_defi.hyperliquid.trade_history_db import (
    DEFAULT_TRADE_HISTORY_DB_PATH,
    HyperliquidTradeHistoryDatabase,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    db_path_str = os.environ.get("TRADE_HISTORY_DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_TRADE_HISTORY_DB_PATH

    addresses_str = os.environ.get("ADDRESSES", "").strip()
    addresses = [a.strip().lower() for a in addresses_str.split(",") if a.strip()]

    labels_str = os.environ.get("LABELS", "").strip()
    labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else []

    min_peak_tvl_str = os.environ.get("MIN_VAULT_PEAK_TVL", "").strip()
    interactive = os.environ.get("INTERACTIVE", "true").strip().lower() != "false"

    max_workers = int(os.environ.get("MAX_WORKERS", "1"))

    print("Hyperliquid trade history sync")
    print(f"DuckDB path: {db_path}")
    print(f"Max workers: {max_workers}")

    # Auto-discover vaults by peak TVL
    if min_peak_tvl_str and not addresses:
        min_peak_tvl = float(min_peak_tvl_str)

        parquet_path_str = os.environ.get("PARQUET_PATH", "").strip()
        parquet_path = Path(parquet_path_str).expanduser() if parquet_path_str else None

        vaults = fetch_vaults_by_peak_tvl(
            min_peak_tvl=min_peak_tvl,
            parquet_path=parquet_path,
        )

        if not vaults:
            print(f"\nNo vaults found with peak TVL >= ${min_peak_tvl:,.0f}")
            return

        # Display discovered vaults
        print(f"\nVaults with peak TVL >= ${min_peak_tvl:,.0f}:")
        print()
        discovery_rows = [[v["name"] or v["address"][:16], v["address"][:16] + "...", f"${v['current_tvl']:,.0f}", f"${v['peak_tvl']:,.0f}"] for v in vaults]
        print(
            tabulate(
                discovery_rows,
                headers=["Name", "Address", "Current TVL", "Peak TVL"],
                tablefmt="simple",
            )
        )
        print(f"\nTotal: {len(vaults)} vaults")

        # Interactive confirmation
        if not interactive:
            proceed = True
        else:
            try:
                answer = input("\nProceed with sync? [y/N] ").strip().lower()
                proceed = answer == "y"
            except (EOFError, KeyboardInterrupt):
                proceed = False

        if not proceed:
            print("Aborted.")
            sys.exit(0)

        # Convert to addresses + labels for the sync
        addresses = [v["address"] for v in vaults]
        labels = [v["name"] for v in vaults if v["name"]]

    # Load proxies if WEBSHARE_API_KEY is set
    rotator = load_proxy_rotator()
    if rotator:
        print_proxy_dashboard(rotator)
    else:
        print("Proxies: disabled (set WEBSHARE_API_KEY to enable)")

    session = create_hyperliquid_session(
        requests_per_second=2.75,
        rotator=rotator,
    )
    db = HyperliquidTradeHistoryDatabase(db_path)

    try:
        # Add specified addresses to whitelist
        if addresses:
            for i, addr in enumerate(addresses):
                label = labels[i] if i < len(labels) else None
                db.add_account(addr, label=label, is_vault=True)
            print(f"Added {len(addresses)} addresses to whitelist")

        accounts = db.get_accounts()
        if not accounts:
            print("No whitelisted accounts. Set ADDRESSES or MIN_VAULT_PEAK_TVL to add accounts.")
            return

        print(f"\nSyncing {len(accounts)} whitelisted accounts...")

        interrupted = False
        results = {}
        try:
            results = db.sync_all(session, max_workers=max_workers)
            db.save()
        except KeyboardInterrupt:
            interrupted = True
            print("\n\nInterrupted — saving checkpoint...")
            db.save()

        # Per-account summary table (only when we have results from a full run)
        if results:
            rows = []
            for account in accounts:
                addr = account["address"]
                r = results.get(addr, {})
                state = db.get_sync_state(addr)
                rows.append(
                    [
                        account.get("label") or addr[:16],
                        addr[:16] + "...",
                        r.get("fills", 0),
                        r.get("funding", 0),
                        r.get("ledger", 0),
                        state.get("fills", {}).get("row_count", 0),
                        state.get("funding", {}).get("row_count", 0),
                        state.get("ledger", {}).get("row_count", 0),
                        "ERR" if r.get("error") else "OK",
                    ]
                )

            print(
                "\n"
                + tabulate(
                    rows,
                    headers=["Label", "Address", "New fills", "New funding", "New ledger", "Total fills", "Total funding", "Total ledger", "Status"],
                    tablefmt="simple",
                )
            )

        # Always print grand totals (even on Ctrl+C)
        totals = db.get_total_row_counts()
        print(f"\nDatabase totals: {totals['fills']} fills, {totals['funding']} funding, {totals['ledger']} ledger")
        print(f"Database path: {db_path}")

        if interrupted:
            sys.exit(130)

    finally:
        db.close()


if __name__ == "__main__":
    main()
