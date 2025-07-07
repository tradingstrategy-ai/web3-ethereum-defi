"""Read timestamps of blocks using multiprocess."""
import datetime
import threading

from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.timestamp import get_block_timestamp

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
) -> dict[int, datetime.datetime]:
    """Extract timesstamps using multiprocessing.

    - Subprocess entrypoint
    - This is called by a joblib.Parallel
    - The subprocess is recycled between different batch jobs
    - We cache reader Web3 connections between batch jobs
    - joblib never shuts down this process
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
            desc=f"Reading timestamps for chain {chain_name}: {start_block:,} - {end_block:,}",
        )
    else:
        progress_bar = None

    def _task_gen():
        nonlocal web3factory
        for block_number in range(start_block, end_block + 1, step):
            yield web3factory, chain_id, block_number

    result = {}

    for completed_task in worker_processor(delayed(_read_timestamp_subprocess)(*args) for args in _task_gen()):
        block_number, timestamp = completed_task
        result[block_number] = timestamp

        if progress_bar:
            progress_bar.update(1)
            progress_bar.set_postfix({
                "timestamp": timestamp,
            })

    if progress_bar:
        progress_bar.close()

    return result


