"""Show Hyperliquid vaults with the smallest leader fraction.

Hyperliquid requires vault leaders to maintain at least 10% of total vault
capital. Vaults where the leader's share is close to this minimum are unlikely
to accept new deposits.

This script reads an existing DuckDB database (populated by
``daily-vault-metrics.py``) and displays the top N user-created vaults sorted
by ascending leader fraction.

Protocol vaults (HLP parent and child sub-vaults) are excluded because they
do not have a leader in the same sense as user-created vaults.

Usage:

.. code-block:: shell

    # Default: show top 10
    poetry run python scripts/hyperliquid/smallest-leader-fraction.py

    # Show top 20
    TOP_N=20 poetry run python scripts/hyperliquid/smallest-leader-fraction.py

    # Custom database path
    DB_PATH=/tmp/daily-metrics.duckdb poetry run python scripts/hyperliquid/smallest-leader-fraction.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ``~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb``
- ``TOP_N``: Number of vaults to display. Default: 10

"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    db_path_str = os.environ.get("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str).expanduser()
    else:
        db_path = HYPERLIQUID_DAILY_METRICS_DATABASE

    top_n = int(os.environ.get("TOP_N", "10"))

    print(f"Reading DuckDB: {db_path}")

    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        metadata_df = db.get_all_vault_metadata()
        leader_fractions = db.get_latest_leader_fractions()

        # Exclude system vaults (HLP parent + child sub-vaults)
        user_vaults = metadata_df[~metadata_df["relationship_type"].isin(["parent", "child"])]
        total_user_vaults = len(user_vaults)

        # Map leader fractions to user vaults
        user_vaults = user_vaults.copy()
        user_vaults["leader_fraction"] = user_vaults["vault_address"].map(leader_fractions)

        with_fraction = user_vaults["leader_fraction"].notna().sum()
        without_fraction = total_user_vaults - with_fraction
        system_vaults = len(metadata_df) - total_user_vaults

        print(f"\nTotal vaults in database: {len(metadata_df)}")
        print(f"System vaults (HLP parent/child, excluded): {system_vaults}")
        print(f"User vaults with leader fraction data: {with_fraction} / {total_user_vaults} ({without_fraction} without data)")

        # Filter to vaults with leader fraction data and sort ascending
        ranked = user_vaults[user_vaults["leader_fraction"].notna()].sort_values("leader_fraction")
        top = ranked.head(top_n)[["name", "vault_address", "tvl", "leader_fraction"]].copy()

        if top.empty:
            print("\nNo vaults with leader fraction data found.")
            return

        # Format for display
        top["tvl"] = top["tvl"].apply(lambda x: f"${x:,.0f}" if x is not None and x == x else "")
        top["leader_fraction"] = top["leader_fraction"].apply(lambda x: f"{x * 100:.2f}%")

        print(f"\nTop {min(top_n, len(top))} vaults by smallest leader fraction:")
        table = tabulate(
            top.to_dict("records"),
            headers="keys",
            tablefmt="fancy_grid",
        )
        print(table)
    finally:
        db.close()


if __name__ == "__main__":
    main()
