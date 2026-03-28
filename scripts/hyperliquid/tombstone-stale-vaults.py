"""Write tombstone rows for Hyperliquid vaults with stale data.

Finds vaults in the local DuckDB whose last daily price row is older than
a staleness threshold.  For each stale vault, fetches the current TVL from
the Hyperliquid bulk stats-data API and displays a comparison table.

These vaults typically became stale because they dropped below the pipeline's
TVL processing threshold — the daily scan stopped fetching new data, but
the last price row still carries the old (higher) TVL, which causes
downstream staleness checks to flag them.

Only vaults whose current API TVL is below ``SAFE_TVL`` are tombstoned.
Vaults with significant current TVL are skipped — they are stale due to a
transient pipeline issue and should be rescanned rather than tombstoned.

For each confirmed stale vault, a tombstone daily price row is written for
today with ``tvl=0`` and ``data_source='tombstone'``.  The ``is_closed``
and ``allow_deposits`` fields are left as ``None`` so the existing
forward-filled state is preserved.

Usage:

.. code-block:: shell

    poetry run python scripts/hyperliquid/tombstone-stale-vaults.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``DB_PATH``: Path to DuckDB. Default: ~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb
- ``STALENESS_DAYS``: Only consider vaults whose last data is older than this many days. Default: 7
- ``SAFE_TVL``: Maximum current API TVL for a vault to be eligible for tombstoning. Default: 5000
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import fetch_all_vaults
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))

    db_path = Path(os.environ.get("DB_PATH", str(HYPERLIQUID_DAILY_METRICS_DATABASE)))
    staleness_days = int(os.environ.get("STALENESS_DAYS", "7"))
    safe_tvl = float(os.environ.get("SAFE_TVL", "5000"))

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
                 ORDER BY vdp2.date DESC LIMIT 1) AS last_written_tvl
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

        # 3. Build table and filter by current TVL safety guard
        headers = ["Name", "Address", "Last date", "Age (days)", "Last written TVL", "Current TVL", "Eligible"]
        today = native_datetime_utc_now().date()
        table_data = []
        eligible_addresses = []
        skipped_count = 0

        for row in stale_rows:
            addr, name, last_date, last_written_tvl = row
            age = (today - last_date).days
            api_vault = api_by_addr.get(addr)
            if api_vault is not None:
                current_tvl_value = float(api_vault.tvl)
                current_tvl_str = f"${current_tvl_value:,.0f}"
            else:
                current_tvl_value = 0.0
                current_tvl_str = "$0 (gone)"

            # Only tombstone vaults with low current TVL
            eligible = current_tvl_value < safe_tvl
            if eligible:
                eligible_addresses.append(addr)
            else:
                skipped_count += 1

            table_data.append(
                [
                    name,
                    addr[:10] + "..." + addr[-4:],
                    str(last_date),
                    age,
                    f"${last_written_tvl:,.0f}",
                    current_tvl_str,
                    "yes" if eligible else f"NO (>{safe_tvl:,.0f})",
                ]
            )

        print(f"\nStale vaults without tombstone (last data > {staleness_days} days):\n")
        print(tabulate(table_data, headers=headers, tablefmt="simple"))
        print(f"\nTotal: {len(stale_rows)} vault(s), {len(eligible_addresses)} eligible, {skipped_count} skipped (TVL > ${safe_tvl:,.0f})")

        if not eligible_addresses:
            print("\nNo eligible vaults to tombstone.")
            return

        # 4. Ask for confirmation
        answer = input(f"\nWrite tombstone rows for {len(eligible_addresses)} eligible vault(s)? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

        # 5. Write tombstone rows using the shared helper
        count = db._write_tombstone_rows(eligible_addresses)
        db.save()

        print(f"\nWrote {count} tombstone row(s).")
        logger.info("Tombstone rows written for %d vaults", count)

    finally:
        db.close()


if __name__ == "__main__":
    main()
