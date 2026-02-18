"""Heal gaps in the block timestamp DuckDB cache.

The block timestamp cache (``~/.tradingstrategy/block-timestamp/{chain_id}-timestamps.duckdb``)
is populated by HyperSync during vault price scanning. On fast chains like Monad,
the HyperSync stream can drop blocks when it times out or reconnects mid-stream.
This leaves the DuckDB database with a contiguous range (first block .. last block)
but with missing entries inside — gaps.

These gaps cause ``KeyError`` crashes in the vault price scanner: the scanner
iterates blocks at step intervals (e.g. every 720 blocks for hourly on Monad)
and looks up each timestamp from the cache. If a block falls inside a gap,
``BlockTimestampSlicer`` cannot find it and raises an error.

This script:

1. Opens the existing DuckDB timestamp database for the chain
2. Detects all gaps using DuckDB window functions (``LEAD`` over block_number)
3. Displays a summary table of gaps with block ranges, timestamps, and sizes
4. Re-fetches the missing block ranges from HyperSync
5. Inserts the recovered timestamps into the existing database
6. Verifies that all gaps have been healed

Usage:

.. code-block:: shell

    # Diagnose gaps without healing
    DRY_RUN=true JSON_RPC_URL=$JSON_RPC_MONAD poetry run python scripts/erc-4626/heal-timestamps.py

    # Heal gaps
    JSON_RPC_URL=$JSON_RPC_MONAD poetry run python scripts/erc-4626/heal-timestamps.py

"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, load_timestamp_cache
from eth_defi.hypersync.hypersync_timestamp import get_block_timestamps_using_hypersync_async
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import from_unix_timestamp, setup_console_logging

logger = logging.getLogger(__name__)

JSON_RPC_URL = os.environ.get("JSON_RPC_URL")
assert JSON_RPC_URL, "JSON_RPC_URL environment variable must be set"


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/heal-timestamps.log"),
    )

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)

    print(f"Healing timestamp database for chain {chain_name} (chain_id={chain_id})")

    # Load existing database
    timestamp_db = load_timestamp_cache(chain_id, DEFAULT_TIMESTAMP_CACHE_FOLDER)
    first, last = timestamp_db.get_first_and_last_block()
    count = timestamp_db.get_count()
    expected = last - first + 1

    print(f"Database range: {first:,} - {last:,}")
    print(f"Total records: {count:,} / {expected:,} expected ({expected - count:,} missing, {(expected - count) / expected * 100:.2f}% gaps)")

    # Detect gaps
    gaps = timestamp_db.find_gaps()

    if not gaps:
        print("No gaps found, database is healthy")
        timestamp_db.close()
        return

    total_missing = sum(g[2] for g in gaps)
    print(f"Found {len(gaps):,} gaps totalling {total_missing:,} missing blocks\n")

    # Build display table with timestamps for gap boundaries
    table_rows = []
    for gap_start, gap_end, gap_size in gaps:
        # Look up timestamps for the gap boundary blocks
        start_ts_series = timestamp_db.query(gap_start, gap_start + 1)
        end_ts_series = timestamp_db.query(gap_end, gap_end + 1)

        start_ts = start_ts_series.iloc[0] if len(start_ts_series) > 0 else "?"
        end_ts = end_ts_series.iloc[0] if len(end_ts_series) > 0 else "?"

        table_rows.append(
            [
                f"{gap_start:,}",
                str(start_ts),
                f"{gap_end:,}",
                str(end_ts),
                f"{gap_size:,}",
            ]
        )

    print(
        tabulate(
            table_rows,
            headers=["Gap start block", "Start timestamp", "Gap end block", "End timestamp", "Missing blocks"],
            tablefmt="grid",
        )
    )

    if dry_run:
        print("\nDry run mode, not healing gaps")
        timestamp_db.close()
        return

    # Configure HyperSync client
    hypersync_server = get_hypersync_server(chain_id)
    assert hypersync_server, f"No HyperSync server configured for chain {chain_name} ({chain_id})"

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY")
    hypersync_client = hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url=hypersync_server,
            bearer_token=hypersync_api_key,
        )
    )

    print(f"\nHealing {len(gaps):,} gaps using HyperSync server {hypersync_server}...")

    async def _heal_gaps():
        healed = 0
        for i, (gap_start, gap_end, gap_size) in enumerate(gaps):
            # Fetch the missing range: gap_start+1 to gap_end-1 (the blocks inside the gap)
            fetch_start = gap_start + 1
            fetch_end = gap_end - 1

            if fetch_start > fetch_end:
                continue

            logger.info("Healing gap %d/%d: blocks %d - %d (%d missing)", i + 1, len(gaps), fetch_start, fetch_end, gap_size)

            index = []
            values = []

            async for block_header in get_block_timestamps_using_hypersync_async(
                hypersync_client,
                chain_id,
                start_block=fetch_start,
                end_block=fetch_end,
                display_progress=False,
            ):
                index.append(block_header.block_number)
                values.append(block_header.timestamp)

            if index:
                series = pd.Series(data=values, index=index)
                timestamp_db.import_chain_data(chain_id, series)
                healed += len(index)
                logger.info("Inserted %d timestamps for gap %d/%d", len(index), i + 1, len(gaps))
            else:
                logger.warning("HyperSync returned no blocks for gap %d/%d (blocks %d-%d)", i + 1, len(gaps), fetch_start, fetch_end)

        return healed

    healed_count = asyncio.run(_heal_gaps())
    print(f"\nInserted {healed_count:,} timestamps")

    # Verify
    remaining_gaps = timestamp_db.find_gaps()
    remaining_missing = sum(g[2] for g in remaining_gaps)
    new_count = timestamp_db.get_count()

    print(f"\nAfter healing:")
    print(f"  Total records: {new_count:,} / {expected:,}")
    if remaining_gaps:
        print(f"  Remaining gaps: {len(remaining_gaps):,} ({remaining_missing:,} missing blocks)")
    else:
        print("  No gaps remaining, database is fully healed")

    timestamp_db.close()
    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
