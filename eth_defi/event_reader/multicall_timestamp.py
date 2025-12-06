"""Read timestamps of blocks using multiprocess."""

import datetime
import logging
import threading
from pathlib import Path

import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.timestamp_cache import load_timestamp_cache, BlockTimestampDatabase, DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampSlicer
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
    return block_number, get_block_timestamp(web3, block_number, raw=True)


def fetch_block_timestamps_multiprocess(
    chain_id: int,
    web3factory: Web3Factory,
    start_block: int,
    end_block: int,
    step: int,
    display_progress=True,
    max_workers=8,
    timeout=120,
    cache_path: Path | None = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    checkpoint_freq: int = 20_000,
) -> BlockTimestampSlicer:
    """Extract timestamps using fast multiprocessing.

    - Subprocess entrypoint
    - This is called by a joblib.Parallel
    - The subprocess is recycled between different batch jobs
    - We cache reader Web3 connections between batch jobs
    - joblib never shuts down this process

    .. note ::

        Because this method aggressively uses `step` to skip blocks,
        it results to non-reuseable timestamp cache (only valid for one scan and subsequent scans of the same task).

    :param cache_path
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
            desc=f"Reading timestamps (slow) for chain {chain_name}: {start_block:,} - {end_block:,}, step {step}, {max_workers} workers",
        )
    else:
        progress_bar = None

    timestamp_db = None  # Allow operating without caching
    if cache_path:
        if cache_path.exists():
            timestamp_db: BlockTimestampDatabase = load_timestamp_cache(chain_id, cache_path)
        else:
            timestamp_db = BlockTimestampDatabase.create(chain_id, cache_path)

        result = timestamp_db.get_slicer()
    else:
        result = {}

    def _task_gen():
        nonlocal web3factory
        first_block_to_check = max(start_block, timestamp_db.get_last_block())
        for _block_number in range(first_block_to_check, end_block + 1, step):
            if result.get(_block_number) is None:
                yield web3factory, chain_id, _block_number

    last_save = block_number = 0

    def _save():
        # Periodical checkpoint write
        nonlocal last_save
        nonlocal block_number
        nonlocal index
        nonlocal values
        last_save = block_number
        if index:
            series = pd.Series(data=values, index=index)
            timestamp_db.import_chain_data(
                chain_id,
                series,
            )
        index = []
        values = []

    index = []
    values = []

    # Because of asyncrhonoisty issues with new DuckDB cache, we need to buffer all tasks and reads in one go
    tasks = list(_task_gen())
    for completed_task in worker_processor(delayed(_read_timestamp_subprocess)(*args) for args in tasks):
        block_number, timestamp = completed_task

        index.append(block_number)
        values.append(timestamp)

        if progress_bar:
            progress_bar.update(1)
            progress_bar.set_postfix(
                {
                    "timestamp": timestamp,
                }
            )

        if block_number - last_save >= checkpoint_freq:
            # Save the current state to the cache file
            _save()

    # Final checkpoint
    _save()

    if progress_bar:
        progress_bar.close()

    if timestamp_db:
        block_range = timestamp_db.get_first_and_last_block()
        count = timestamp_db.get_count()
        logger.info(f"Timestamp cache {cache_path} populated for chain {chain_id}: blocks {block_range[0]:,} - {block_range[1]:,}, total {count:,} entries")
        return timestamp_db.get_slicer()
    else:
        raise NotImplementedError("Non-cached timestamp fetching not implemented")


def fetch_block_timestamps_multiprocess_auto_backend(
    chain_id: int,
    web3factory: Web3Factory,
    start_block: int,
    end_block: int,
    step: int,
    display_progress=True,
    max_workers=8,
    timeout=120,
    cache_path: Path | None = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    checkpoint_freq: int = 20_000,
    hypersync_client: "hypersync.HypersyncClient | None" = None,
) -> BlockTimestampSlicer:
    """Fetch block timestamps, choose backend.

    - If Hypersync is available, use the optimised code path

    For arguments see :py:func:`fetch_block_timestamps_multiprocess`.

    :param step:
        Hypersync does not respect `step` but gets all blocks.

    :return:
        Pandas series block number (int) -> block timestamp (datetime)
    """

    if hypersync_client:
        from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached

        return fetch_block_timestamps_using_hypersync_cached(
            client=hypersync_client,
            chain_id=chain_id,
            start_block=start_block,
            end_block=end_block,
            cache_path=cache_path,
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
            cache_path=cache_path,
            checkpoint_freq=checkpoint_freq,
        )
