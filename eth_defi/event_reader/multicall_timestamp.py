"""Read timestamps of blocks using multiprocess."""

import datetime
import logging
import os
import pickle
import threading
from pathlib import Path
from typing import TypeAlias

from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.timestamp import get_block_timestamp


logger = logging.getLogger(__name__)


ChainBlockTimestampMap: TypeAlias = dict[int, dict[int, datetime.datetime]]

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


def _load_data(cache_file: Path) -> ChainBlockTimestampMap:
    return pickle.load(cache_file.open("rb")) if cache_file.exists() else {}


def _save_data(timestamps: ChainBlockTimestampMap, cache_file: Path):
    assert isinstance(timestamps, dict), "Timestamps must be a dictionary"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_file.with_suffix(cache_file.suffix + ".tmp")
    with tmp_path.open("wb") as f:
        pickle.dump(timestamps, f)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(cache_file)  # Atomic move

    size = cache_file.stat().st_size
    logger.debug(f"Saved block timestamps to {cache_file}, size is {size / 1024 * 1024} MB")


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
) -> dict[int, datetime.datetime]:
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
            desc=f"Reading timestamps for chain {chain_name}: {start_block:,} - {end_block:,}, {max_workers} workers",
        )
    else:
        progress_bar = None

    existing_data = _load_data(cache_file)

    result: ChainBlockTimestampMap = existing_data
    result[chain_id] = result.get(chain_id, {})

    def _task_gen():
        nonlocal web3factory
        for _block_number in range(start_block, end_block + 1, step):
            if result[chain_id].get(_block_number) is None:
                yield web3factory, chain_id, _block_number

    last_save = 0

    for completed_task in worker_processor(delayed(_read_timestamp_subprocess)(*args) for args in _task_gen()):
        block_number, timestamp = completed_task
        result[chain_id][block_number] = timestamp

        if progress_bar:
            progress_bar.update(1)
            progress_bar.set_postfix(
                {
                    "timestamp": timestamp,
                }
            )

        if block_number - last_save >= checkpoint_freq:
            # Save the current state to the cache file
            last_save = block_number
            if cache_file:
                _save_data(result, cache_file)

    if progress_bar:
        progress_bar.close()

    return result[chain_id]
