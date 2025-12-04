"""Read timestamps of blocks using multiprocess."""

import datetime
import logging
import threading
from pathlib import Path

import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.timestamp_cache import load_timestamp_cache, save_timestamp_cache, BlockTimestampDatabase, DEFAULT_TIMESTAMP_CACHE_FILE
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.timestamp import get_block_timestamp


logger = logging.getLogger(__name__)


_timestamp_instance = threading.local()


def _read_timestamp_subprocess(
    web3factory: Web3Factory,
    chain_id: int,
    block_number: int,
) -> tuple[int, datetime.datetime]:
    # Initialise web3 connection when called for the first time.
    # We will recycle the same connection instance and it is kept open
    # until shutdown.
    per_chain_web3 = getattr(_timestamp_instance, "per_chain_web3", None)
    if per_chain_web3 is None:
        per_chain_web3 = _timestamp_instance.per_chain_readers = {}

    web3 = per_chain_web3.get(chain_id)
    if web3 is None:
        web3 = per_chain_web3[chain_id] = web3factory()

    assert web3.eth.chain_id == chain_id, f"Web3 chain ID mismatch: {web3.eth.chain_id} != {chain_id}"

    return block_number, get_block_timestamp(web3, block_number)


def fetch_block_timestamps_multiprocess(
    chain_id: int,
    web3factory: Web3Factory,
    start_block: int,
    end_block: int,
    step: int,
    display_progress=True,
    max_workers=8,
    timeout=120,
    cache_file: Path | None = Path.home() / ".cache" / "tradingstrategy" / "block-timestamps.pickle",
    checkpoint_freq: int = 20_000,
) -> pd.Series:
    """Extract timestamps using fast multiprocessing.

    - Subprocess entrypoint
    - This is called by a joblib.Parallel
    - The subprocess is recycled between different batch jobs
    - We cache reader Web3 connections between batch jobs
    - joblib never shuts down this process

    :param cache_file
        Cache timestamps across runs and commands.

        Set to ``None`` to disable, or remove the file.
        .
    :param checkpoint_freq:
        Block number frequency how often to save.
    """

    assert start_block <= end_block, f"Start block {start_block} must be less than or equal to end block {end_block}"
    assert step >= 1, f"Step must be at least 1, got {step}"

    chain_name = get_chain_name(chain_id)

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=1 * 1024 * 1024,  # Allow passing 1 MBytes for child processes
        return_as="generator_unordered",
    )

    if display_progress:
        progress_bar = tqdm(
            total=(end_block - start_block) // step,
            desc=f"Reading timestamps (slow) for chain {chain_name}: {start_block:,} - {end_block:,}, {max_workers} workers",
        )
    else:
        progress_bar = None

    timestamp_db = None  # Allow operating without caching
    if cache_file:
        if cache_file.exists():
            timestamp_db: BlockTimestampDatabase = load_timestamp_cache(cache_file)
        else:
            timestamp_db = BlockTimestampDatabase.create(cache_file)

        series = timestamp_db[chain_id]

        if series is not None:
            result = series.to_dict()
        else:
            result = {}
    else:
        result = {}

    def _task_gen():
        nonlocal web3factory
        for _block_number in range(start_block, end_block + 1, step):
            if result.get(_block_number) is None:
                yield web3factory, chain_id, _block_number

    last_save = block_number = 0

    def _save():
        # Periodical checkpoint write
        nonlocal last_save
        nonlocal block_number
        last_save = block_number
        if timestamp_db:
            timestamp_db.import_chain_data(
                chain_id,
                result,
            )
            save_timestamp_cache(timestamp_db, cache_file)

    for completed_task in worker_processor(delayed(_read_timestamp_subprocess)(*args) for args in _task_gen()):
        block_number, timestamp = completed_task
        result[block_number] = timestamp

        if progress_bar:
            progress_bar.update(1)
            progress_bar.set_postfix(
                {
                    "timestamp": timestamp,
                }
            )

        if block_number - last_save >= checkpoint_freq:
            # Save the current state to the cache file
            timestamp_db.import_chain_data(
                chain_id,
                result,
            )
            _save()

    # Final checkpoint
    _save()

    if progress_bar:
        progress_bar.close()

    if timestamp_db:
        try:
            return timestamp_db[chain_id]
        finally:
            # DuckDB save
            timestamp_db.close()
    else:
        return pd.Series(result)


def fetch_block_timestamps_multiprocess_auto_backend(
    chain_id: int,
    web3factory: Web3Factory,
    start_block: int,
    end_block: int,
    step: int,
    display_progress=True,
    max_workers=8,
    timeout=120,
    cache_file: Path | None = DEFAULT_TIMESTAMP_CACHE_FILE,
    checkpoint_freq: int = 20_000,
    hypersync_client: "hypersync.HypersyncClient | None" = None,
) -> pd.Series:
    """Fetch block timestamps, choose backend.

    - If Hypersync is available, use the optimised code path

    For arguments see :py:func:`fetch_block_timestamps_multiprocess`.

    :param step:
        Hypersync does not respect `step` but gets all blocks.

    :return:
        Pandas series block number (int) -> block timestamp (datetime)
    """

    if hypersync_client:
        from eth_defi.hypersync.timestamp import fetch_block_timestamps_using_hypersync_cached

        return fetch_block_timestamps_using_hypersync_cached(
            client=hypersync_client,
            chain_id=chain_id,
            start_block=start_block,
            end_block=end_block,
            cache_file=cache_file,
            display_progress=display_progress,
        )
    else:
        return fetch_block_timestamps_multiprocess(
            chain_id=chain_id,
            web3factory=web3factory,
            start_block=start_block,
            end_block=end_block,
            step=step,
            display_progress=display_progress,
            max_workers=max_workers,
            timeout=timeout,
            cache_file=cache_file,
            checkpoint_freq=checkpoint_freq,
        )
