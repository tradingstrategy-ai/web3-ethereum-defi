"""Sync trade history for whitelisted Hyperliquid accounts.

Fetches fills, funding payments, and ledger events for whitelisted accounts
and stores them in a DuckDB database. Supports incremental sync that
accumulates data beyond the 10K fill API limit.

Usage:

.. code-block:: shell

    # Sync specific addresses
    ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e,0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
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
- ``MAX_WORKERS``: Parallel threads (default: 1, DuckDB single-writer).
"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.session import create_hyperliquid_session
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

    max_workers = int(os.environ.get("MAX_WORKERS", "1"))

    print(f"Hyperliquid trade history sync")
    print(f"DuckDB path: {db_path}")
    print(f"Max workers: {max_workers}")

    session = create_hyperliquid_session(requests_per_second=2.75)
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
            print("No whitelisted accounts. Set ADDRESSES to add accounts.")
            return

        print(f"\nSyncing {len(accounts)} whitelisted accounts...")

        # Sync all accounts
        results = db.sync_all(session, max_workers=max_workers)
        db.save()

        # Summary table
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

    finally:
        db.close()


if __name__ == "__main__":
    main()
