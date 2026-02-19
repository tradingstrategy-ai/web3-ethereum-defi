"""Heal gaps in block timestamp DuckDB caches across all chains.

Scans the timestamp cache folder for existing databases, detects gaps,
and heals them using HyperSync. No RPC URLs needed — chain IDs are
extracted from database filenames and HyperSync servers are resolved
automatically.

Usage:

.. code-block:: shell

    # Heal all chains
    poetry run python scripts/erc-4626/heal-timestamps-all-chains.py

    # Diagnose gaps without healing
    DRY_RUN=true poetry run python scripts/erc-4626/heal-timestamps-all-chains.py

    # Heal specific chains only (by name)
    TEST_CHAINS=Monad,Base poetry run python scripts/erc-4626/heal-timestamps-all-chains.py

Environment variables:
    - DRY_RUN: "true" to only report gaps without healing (default: "false")
    - TEST_CHAINS: Comma-separated chain names to heal (default: all)
    - LOG_LEVEL: Logging level (default: "info")
    - HYPERSYNC_API_KEY: HyperSync API key (optional but recommended)
"""

import asyncio
import logging
import os
import re
import sys
import time
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
from eth_defi.hypersync.server import get_hypersync_server, is_hypersync_supported_chain
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def discover_timestamp_databases(cache_folder: Path) -> list[tuple[int, Path]]:
    """Scan the cache folder for existing timestamp databases.

    :return:
        List of (chain_id, db_path) tuples, sorted by chain_id.
    """
    if not cache_folder.exists():
        return []

    results = []
    for f in cache_folder.glob("*-timestamps.duckdb"):
        match = re.match(r"^(\d+)-timestamps\.duckdb$", f.name)
        if match:
            chain_id = int(match.group(1))
            results.append((chain_id, f))

    return sorted(results, key=lambda x: x[0])


def heal_chain(chain_id: int, dry_run: bool) -> dict:
    """Detect and heal gaps for a single chain.

    :return:
        Result dict with keys: chain_name, total, expected, missing,
        gap_count, healed, duration, status, error.
    """
    chain_name = get_chain_name(chain_id)
    start_time = time.time()

    result = {
        "chain_id": chain_id,
        "chain_name": chain_name,
        "total": 0,
        "expected": 0,
        "missing": 0,
        "gap_count": 0,
        "healed": 0,
        "duration": 0.0,
        "status": "ok",
        "error": None,
    }

    try:
        timestamp_db = load_timestamp_cache(chain_id, DEFAULT_TIMESTAMP_CACHE_FOLDER)
        first, last = timestamp_db.get_first_and_last_block()

        if first == 0 and last == 0:
            result["status"] = "empty"
            timestamp_db.close()
            result["duration"] = time.time() - start_time
            return result

        count = timestamp_db.get_count()
        expected = last - first + 1
        missing = expected - count

        result["total"] = count
        result["expected"] = expected
        result["missing"] = missing

        # Detect gaps
        gaps = timestamp_db.find_gaps()
        result["gap_count"] = len(gaps)

        if not gaps:
            result["status"] = "ok"
            timestamp_db.close()
            result["duration"] = time.time() - start_time
            return result

        if dry_run:
            result["status"] = "gaps_found"
            timestamp_db.close()
            result["duration"] = time.time() - start_time
            return result

        # Check HyperSync support
        if not is_hypersync_supported_chain(chain_id):
            result["status"] = "no_hypersync"
            result["error"] = f"No HyperSync server for chain {chain_id}"
            timestamp_db.close()
            result["duration"] = time.time() - start_time
            return result

        # Configure HyperSync
        hypersync_server = get_hypersync_server(chain_id)
        hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY")
        hypersync_client = hypersync.HypersyncClient(
            hypersync.ClientConfig(
                url=hypersync_server,
                bearer_token=hypersync_api_key,
            )
        )

        async def _heal():
            healed = 0
            for i, (gap_start, gap_end, gap_size) in enumerate(gaps):
                fetch_start = gap_start + 1
                fetch_end = gap_end - 1

                if fetch_start > fetch_end:
                    continue

                logger.info(
                    "%s: Healing gap %d/%d: blocks %d-%d (%d missing)",
                    chain_name,
                    i + 1,
                    len(gaps),
                    fetch_start,
                    fetch_end,
                    gap_size,
                )

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
                else:
                    logger.warning(
                        "%s: HyperSync returned no blocks for gap %d/%d (blocks %d-%d)",
                        chain_name,
                        i + 1,
                        len(gaps),
                        fetch_start,
                        fetch_end,
                    )

            return healed

        healed_count = asyncio.run(_heal())
        result["healed"] = healed_count

        # Verify
        remaining_gaps = timestamp_db.find_gaps()
        if remaining_gaps:
            remaining_missing = sum(g[2] for g in remaining_gaps)
            result["status"] = "partial"
            result["missing"] = remaining_missing
            result["gap_count"] = len(remaining_gaps)
        else:
            result["status"] = "healed"
            result["missing"] = 0
            result["gap_count"] = 0

        timestamp_db.close()

    except Exception as e:
        logger.exception("%s: Failed to heal", chain_name)
        result["status"] = "error"
        result["error"] = str(e)

    result["duration"] = time.time() - start_time
    return result


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/heal-timestamps-all-chains.log"),
    )

    # Filter chains if TEST_CHAINS is set
    test_chains_str = os.environ.get("TEST_CHAINS")
    if test_chains_str:
        test_chain_names = {name.strip() for name in test_chains_str.split(",")}
    else:
        test_chain_names = None

    # Discover existing timestamp databases
    databases = discover_timestamp_databases(DEFAULT_TIMESTAMP_CACHE_FOLDER)

    if not databases:
        print(f"No timestamp databases found in {DEFAULT_TIMESTAMP_CACHE_FOLDER}")
        return

    # Skip chains that do not have a HyperSync server (healing requires HyperSync)
    skipped_no_hypersync = [(cid, path) for cid, path in databases if not is_hypersync_supported_chain(cid)]
    databases = [(cid, path) for cid, path in databases if is_hypersync_supported_chain(cid)]

    if skipped_no_hypersync:
        skipped_names = ", ".join(f"{get_chain_name(cid)} ({cid})" for cid, _ in skipped_no_hypersync)
        logger.info("Skipping chains without HyperSync support: %s", skipped_names)

    # Filter by chain name if TEST_CHAINS is set
    if test_chain_names:
        databases = [(cid, path) for cid, path in databases if get_chain_name(cid) in test_chain_names]
        if not databases:
            print(f"No matching databases for TEST_CHAINS={test_chains_str}")
            all_dbs = discover_timestamp_databases(DEFAULT_TIMESTAMP_CACHE_FOLDER)
            print(f"Available chains: {', '.join(get_chain_name(cid) for cid, _ in all_dbs)}")
            sys.exit(1)

    mode = "DRY RUN" if dry_run else "HEAL"
    print(f"Timestamp gap healing ({mode}) for {len(databases)} chains")
    print(f"Cache folder: {DEFAULT_TIMESTAMP_CACHE_FOLDER}\n")

    # Process each chain
    results = []
    for chain_id, db_path in databases:
        chain_name = get_chain_name(chain_id)
        print(f"Processing {chain_name} (chain_id={chain_id})...")
        result = heal_chain(chain_id, dry_run)
        results.append(result)

    # Build summary table
    table_rows = []
    for r in results:
        if r["status"] == "empty":
            status_str = "empty"
        elif r["status"] == "ok":
            status_str = "ok"
        elif r["status"] == "gaps_found":
            status_str = f"{r['gap_count']} gaps"
        elif r["status"] == "healed":
            status_str = f"healed ({r['healed']:,})"
        elif r["status"] == "partial":
            status_str = f"partial ({r['healed']:,} healed, {r['missing']:,} remaining)"
        elif r["status"] == "no_hypersync":
            status_str = "no HyperSync"
        elif r["status"] == "error":
            status_str = f"error: {r['error'][:50]}"
        else:
            status_str = r["status"]

        gap_pct = f"{r['missing'] / r['expected'] * 100:.2f}%" if r["expected"] > 0 else "-"

        table_rows.append(
            [
                r["chain_name"],
                r["chain_id"],
                f"{r['total']:,}" if r["total"] else "-",
                f"{r['expected']:,}" if r["expected"] else "-",
                f"{r['missing']:,}" if r["missing"] else "0",
                gap_pct,
                f"{r['duration']:.1f}s",
                status_str,
            ]
        )

    print(
        "\n"
        + tabulate(
            table_rows,
            headers=["Chain", "ID", "Records", "Expected", "Missing", "Gap %", "Duration", "Status"],
            tablefmt="grid",
        )
    )

    # Summary line
    total_healed = sum(r["healed"] for r in results)
    total_missing = sum(r["missing"] for r in results)
    chains_with_gaps = sum(1 for r in results if r["gap_count"] > 0)
    chains_ok = sum(1 for r in results if r["status"] == "ok")
    chains_healed = sum(1 for r in results if r["status"] == "healed")
    chains_error = sum(1 for r in results if r["status"] == "error")

    print(f"\nSummary: {chains_ok} healthy, {chains_healed} healed, {chains_with_gaps} with gaps, {chains_error} errors")

    if total_healed > 0:
        print(f"Total timestamps inserted: {total_healed:,}")
    if total_missing > 0:
        print(f"Total blocks still missing: {total_missing:,}")

    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
