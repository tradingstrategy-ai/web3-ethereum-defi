"""Confirm timestamp layout savings on disposable full-file copies.

Defaults to Ethereum and Base. TIMESTAMP_BENCHMARK_CHAINS optionally selects
more local caches; TIMESTAMP_CACHE selects the source directory, and OUTPUT_DIR
selects an ignored report directory. Run with an OS deadline (see the runbook).
Original databases are opened read-only; all ingestion uses disposable copies.
"""

import json
import logging
import os
import resource
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampDatabase
from eth_defi.event_reader.timestamp_lock import timestamp_access_lock
from eth_defi.event_reader.timestamp_storage import build_timestamp_candidate, inspect_timestamp_file, storage_connection, storage_progress

logger = logging.getLogger(__name__)


def benchmark(source: Path, output: Path, chain_id: int) -> dict:
    """Compare full-file layouts and repeated scanner-style ingestion cycles.

    Source contents are exactly checked before testing disposable candidates.
    Wall times are warm-cache observations on a shared machine.

    :param source: Quiescent timestamp file.
    :param output: Run directory for reports and scratch copies.
    :param chain_id: Chain identity used by the cache API.
    :return: JSON-safe size, integrity, timing and ingestion observations.
    """
    with tempfile.TemporaryDirectory(dir=output, prefix=f"chain-{chain_id}-") as temporary, timestamp_access_lock(source.parent), storage_progress(f"benchmark chain {chain_id}"):
        scratch = Path(temporary)
        original = inspect_timestamp_file(source, scratch)
        report = dict(chain_id=chain_id, source=original, layouts={})
        for ordered in (False, True):
            name = "sorted" if ordered else "unsorted"
            target = scratch / f"{name}.duckdb"
            started = time.monotonic()
            stats = build_timestamp_candidate(source, target, scratch, ordered=ordered)
            stats["seconds"] = time.monotonic() - started
            timings = {}
            with storage_connection(target, scratch) as con:
                for typed in (False, True):
                    for width in (1_000, 1_000_000):
                        times = []
                        for fraction in (0.1, 0.5, 0.9):
                            lower = int((original["first"] or 0) + ((original["last"] or 0) - (original["first"] or 0)) * fraction)
                            for _ in range(3):
                                started = time.monotonic()
                                bounds = "?::UBIGINT AND ?::UBIGINT" if typed else "? AND ?"
                                con.execute(f"SELECT * FROM block_timestamps WHERE block_number BETWEEN {bounds} ORDER BY block_number", [lower, lower + width]).df()
                                times.append(time.monotonic() - started)
                        timings[f"{'unsigned' if typed else 'signed'}_{width}"] = statistics.median(times)
            stats["query_seconds"] = timings
            report["layouts"][name] = stats
        target = scratch / "sorted.duckdb"
        database = BlockTimestampDatabase(chain_id, target)
        try:
            database.con.execute("SET memory_limit = '2GB'")
            database.con.execute("SET threads = 4")
            api_times = {}
            for width in (1_000, 1_000_000):
                samples = []
                for _ in range(3):
                    started = time.monotonic()
                    database.query(original["first"] or 0, (original["first"] or 0) + width)
                    samples.append(time.monotonic() - started)
                api_times[f"query_{width}"] = statistics.median(samples)
            started = time.monotonic()
            database.get_missing_block_numbers([original["first"] or 0, original["last"] or 0, (original["last"] or 0) + 1])
            api_times["sparse_lookup"] = time.monotonic() - started
            started = time.monotonic()
            database.find_gaps()
            api_times["gap_discovery"] = time.monotonic() - started
            if original["rows"]:
                started = time.monotonic()
                database.get_slicer().get(original["last"])
                api_times["slicer_tail"] = time.monotonic() - started
            report["api_seconds"] = api_times
        finally:
            database.close()
        for cycle in range(5):
            database = BlockTimestampDatabase(chain_id, target)
            try:
                end = database.get_last_block()
                incoming = pd.Series(1_800_000_000 + pd.RangeIndex(100_000).to_numpy() // 2, index=pd.RangeIndex(end + 1, end + 100_001))
                database.import_chain_data(chain_id, incoming.iloc[::-1])
                database.import_chain_data(chain_id, incoming)
                corrected = incoming.iloc[:10] + 1
                database.import_chain_data(chain_id, corrected)
                assert database.query(end + 1, end + 10).equals(pd.Series(pd.to_datetime(corrected, unit="s").astype("datetime64[s]"), index=corrected.index))
                assert database.get_count() == original["rows"] + (cycle + 1) * 100_000
            finally:
                database.close()
            logger.info("Chain %s ingestion cycle %s complete", chain_id, cycle + 1)
        # Append one genuinely historical missing block, after the five tail
        # cycles, to exercise overlap with older row groups.
        if original["first"] and original["first"] > 0:
            database = BlockTimestampDatabase(chain_id, target)
            try:
                historical = pd.Series([1_700_000_000], index=[original["first"] - 1])
                database.import_chain_data(chain_id, historical)
                database.import_chain_data(chain_id, historical)
                assert database.get_count() == original["rows"] + 500_001
            finally:
                database.close()
        report["after_ingestion"] = inspect_timestamp_file(target, scratch)
        fresh = build_timestamp_candidate(target, scratch / "post-ingestion.duckdb", scratch)
        actual = report["after_ingestion"]
        assert actual["free_bytes"] <= max(16 * 1024**2, actual["live_bytes"] / 4)
        assert actual["bytes"] <= max(16 * 1024**2, fresh["bytes"] * 2)
        report["post_ingestion_fresh_bytes"] = fresh["bytes"]
        report["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["free_disk_bytes"] = shutil.disk_usage(output).free
        return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    output = Path(os.environ.get("OUTPUT_DIR", "logs/timestamp-storage"))
    output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(dir=output, prefix="run-"))
    logger.info("Reports: %s", output)
    for chain in tqdm([int(value) for value in os.environ.get("TIMESTAMP_BENCHMARK_CHAINS", "1,8453").split(",")]):
        result = benchmark(root / f"{chain}-timestamps.duckdb", output, chain)
        (output / f"{chain}.json").write_text(json.dumps(result, indent=2) + "\n")
        logger.info("Chain %s passed: %s", chain, json.dumps(result))
