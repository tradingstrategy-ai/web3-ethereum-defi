"""Write tombstone rows for Hyperliquid vaults with stale data.

Finds vaults in the local DuckDB whose last daily price row is older than
a staleness threshold.  For each stale vault, fetches the current TVL from
the Hyperliquid bulk stats-data API and displays a comparison table.

These vaults typically became stale because they dropped below the pipeline's
TVL processing threshold — the daily scan stopped fetching new data, but
the last price row still carries the old (higher) TVL, which causes
downstream staleness checks to flag them.

For each confirmed stale vault, a tombstone daily price row is written for
today with ``tvl=0``, ``is_closed=True``, ``allow_deposits=False``, and
``data_source='tombstone'``.

Usage:

.. code-block:: shell

    poetry run python scripts/hyperliquid/tombstone-stale-vaults.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``DB_PATH``: Path to DuckDB. Default: ~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb
- ``STALENESS_DAYS``: Only consider vaults whose last data is older than this many days. Default: 7
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase, HyperliquidDailyPriceRow
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import fetch_all_vaults
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))

    db_path = Path(os.environ.get("DB_PATH", str(HYPERLIQUID_DAILY_METRICS_DATABASE)))
    staleness_days = int(os.environ.get("STALENESS_DAYS", "7"))

    # 1. Open the DuckDB and fetch the current bulk listing from the API
    logger.info("Opening database at %s", db_path)
    db = HyperliquidDailyMetricsDatabase(db_path)

    try:
        session = create_hyperliquid_session(requests_per_second=2.75)
        logger.info("Fetching bulk vault listing from Hyperliquid stats-data API...")
        api_vaults = list(fetch_all_vaults(session))
        api_by_addr = {v.vault_address.lower(): v for v in api_vaults}
        logger.info("API returned %d vaults", len(api_vaults))

        # 2. Find stale vaults: last daily price older than threshold, no tombstone yet
        cutoff = native_datetime_utc_now().date() - datetime.timedelta(days=staleness_days)

        stale_rows = db.con.execute(
            """
            SELECT
                vm.vault_address,
                vm.name,
                MAX(vdp.date) AS last_date,
                (SELECT vdp2.tvl FROM vault_daily_prices vdp2
                 WHERE vdp2.vault_address = vm.vault_address
                 ORDER BY vdp2.date DESC LIMIT 1) AS last_written_tvl,
                (SELECT vdp2.share_price FROM vault_daily_prices vdp2
                 WHERE vdp2.vault_address = vm.vault_address
                 ORDER BY vdp2.date DESC LIMIT 1) AS last_share_price,
                (SELECT vdp2.cumulative_pnl FROM vault_daily_prices vdp2
                 WHERE vdp2.vault_address = vm.vault_address
                 ORDER BY vdp2.date DESC LIMIT 1) AS last_cumulative_pnl
            FROM vault_metadata vm
            JOIN vault_daily_prices vdp ON vm.vault_address = vdp.vault_address
            WHERE vdp.vault_address NOT IN (
                SELECT DISTINCT vault_address FROM vault_daily_prices WHERE data_source = 'tombstone'
            )
            GROUP BY vm.vault_address, vm.name
            HAVING MAX(vdp.date) < ?
            ORDER BY MAX(vdp.date) DESC
            """,
            [cutoff],
        ).fetchall()

        if not stale_rows:
            print(f"\nNo stale vaults found (threshold: {staleness_days} days).")
            return

        # 3. Build and display the table
        headers = ["Name", "Address", "Last date", "Age (days)", "Last written TVL", "Current TVL"]
        today = native_datetime_utc_now().date()
        table_data = []
        for row in stale_rows:
            addr, name, last_date, last_written_tvl, _sp, _cpnl = row
            age = (today - last_date).days
            api_vault = api_by_addr.get(addr)
            if api_vault is not None:
                current_tvl = f"${api_vault.tvl:,.0f}"
            else:
                current_tvl = "$0 (gone)"
            table_data.append(
                [
                    name,
                    addr[:10] + "..." + addr[-4:],
                    str(last_date),
                    age,
                    f"${last_written_tvl:,.0f}",
                    current_tvl,
                ]
            )

        print(f"\nStale vaults without tombstone (last data > {staleness_days} days):\n")
        print(tabulate(table_data, headers=headers, tablefmt="simple"))
        print(f"\nTotal: {len(stale_rows)} vault(s)")

        # 4. Ask for confirmation
        answer = input("\nWrite tombstone rows for these vaults? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

        # 5. Write tombstone rows
        now = native_datetime_utc_now()
        tombstone_rows = []
        for row in stale_rows:
            addr, name, _last_date, _last_tvl, share_price, cumulative_pnl = row
            tombstone_rows.append(
                HyperliquidDailyPriceRow(
                    vault_address=addr,
                    date=today,
                    share_price=share_price,
                    tvl=0.0,
                    cumulative_pnl=cumulative_pnl,
                    daily_pnl=0.0,
                    daily_return=0.0,
                    follower_count=0,
                    is_closed=True,
                    allow_deposits=False,
                    data_source="tombstone",
                    written_at=now,
                )
            )

        db.upsert_daily_prices(tombstone_rows)
        db.save()

        print(f"\nWrote {len(tombstone_rows)} tombstone row(s).")
        logger.info("Tombstone rows written for %d vaults", len(tombstone_rows))

    finally:
        db.close()


if __name__ == "__main__":
    main()
