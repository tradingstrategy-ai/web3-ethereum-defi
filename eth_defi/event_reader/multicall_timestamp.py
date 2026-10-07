"""Read exact sampled block timestamps using cache-aware backends."""

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampDatabase, BlockTimestampSlicer, load_timestamp_cache
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.timestamp import get_block_timestamp

if TYPE_CHECKING:
    import hypersync

logger = logging.getLogger(__name__)

#: Use exact sampled Hypersync reads when less than one per 100 blocks is needed.
HYPERSYNC_SPARSE_TIMESTAMP_MIN_STEP = 100

_timestamp_instance = threading.local()


def _read_timestamp_subprocess(
    web3factory: Web3Factory,
    chain_id: int,
    block_number: int,
    collect_rpc_request_stats: bool = False,
) -> tuple[int, int, RPCRequestStats | None]:
    """Fetch one exact block header using the worker's own Web3 connection.

    The historical helper name is retained. Each thread owns its connection
    and request accumulator; successful requests are returned for parent-side
    accounting. The provider uses ``eth_getBlockByNumber`` as documented in
    the `JSON-RPC API <https://ethereum.org/en/developers/docs/apis/json-rpc/#eth_getblockbynumber>`__.

    :param web3factory: Operator-configured provider factory.
    :param chain_id: Expected provider chain ID.
    :param block_number: Exact block header to fetch.
    :param collect_rpc_request_stats: Attach a per-task RPC accumulator.
    :return: Block number, integer Unix timestamp and optional request counts.
    """
    # Initialise web3 connection when called for the first time.
    # We will recycle the same connection instance and it is kept open
    # until shutdown.
    per_chain_web3 = getattr(_timestamp_instance, "per_chain_web3", None)
    if per_chain_web3 is None:
        per_chain_web3 = _timestamp_instance.per_chain_web3 = {}

    task_rpc_request_stats = RPCRequestStats(operation="timestamp") if collect_rpc_request_stats else None

    web3 = per_chain_web3.get(chain_id)
    if web3 is None:
        if isinstance(web3factory, MultiProviderWeb3Factory):
            # Include provider verification requests made on the worker's
            # first thread-local connection in this task's returned totals.
            web3 = web3factory(rpc_request_stats=task_rpc_request_stats)
        else:
            web3 = web3factory()
        per_chain_web3[chain_id] = web3

    set_rpc_request_stats = getattr(web3, "set_rpc_request_stats", None)
    if callable(set_rpc_request_stats):
        set_rpc_request_stats(task_rpc_request_stats)

    try:
        assert web3.eth.chain_id == chain_id, f"Web3 chain ID mismatch: {web3.eth.chain_id} != {chain_id}"
        return block_number, get_block_timestamp(web3, block_number, raw=True), task_rpc_request_stats
    finally:
        if callable(set_rpc_request_stats):
            set_rpc_request_stats(None)


def fetch_block_timestamps_multiprocess(
    chain_id: int,
    web3factory: Web3Factory,
    start_block: int,
    end_block: int,
    step: int,
    display_progress: bool = True,
    max_workers: int = 8,
    timeout: int = 120,
    cache_path: Path | None = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    checkpoint_freq: int = 20_000,
    rpc_request_stats: RPCRequestStats | None = None,
) -> BlockTimestampSlicer:
    """Fetch exact sampled block timestamps using parallel RPC reads.

    The historical function name is retained for callers. Thread-local Web3
    connections read only missing sample blocks and preserve the original
    ``start_block`` sampling grid. A sparse cache's latest block is not a
    completeness boundary, and nearest-block estimates are not cache hits.

    :param chain_id: Chain whose block headers are requested.
    :param web3factory: Factory using the operator's configured RPC providers.
    :param start_block: First sample block, inclusive.
    :param end_block: Last permissible block, inclusive.
    :param step: Positive interval between samples.
    :param display_progress: Display progress for missing samples.
    :param max_workers: Maximum parallel RPC workers.
    :param timeout: Joblib task timeout in seconds.
    :param cache_path: Persistent timestamp-cache directory; required.
    :param checkpoint_freq: Block-number interval between buffered cache writes.
    :param rpc_request_stats: Optional accumulator for actual RPC calls.
    :return: Caller-owned slicer containing exact requested timestamps; close it after use.
    """

    assert start_block <= end_block, f"Start block {start_block} must be less than or equal to end block {end_block}"
    assert step >= 1, f"Step must be at least 1, got {step}"
    if cache_path is None:
        message = "Non-cached timestamp fetching is not implemented"
        raise NotImplementedError(message)

    chain_name = get_chain_name(chain_id)

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="threading",
        timeout=timeout,
        return_as="generator_unordered",
    )

    timestamp_db = load_timestamp_cache(chain_id, cache_path) if cache_path.exists() else BlockTimestampDatabase.create(chain_id, cache_path)
    completed = False
    progress_bar = None
    try:
        requested_blocks = range(start_block, end_block + 1, step)
        missing_blocks = timestamp_db.get_missing_block_numbers(requested_blocks)
        tasks = [(web3factory, chain_id, block, rpc_request_stats is not None) for block in missing_blocks]
        logger.info("Reading %d missing exact timestamps for chain %s (%d sampled blocks cached)", len(tasks), chain_name, len(requested_blocks) - len(tasks))
        if display_progress:
            progress_bar = tqdm(
                total=len(tasks),
                desc=f"Reading timestamps (RPC) for chain {chain_name}: {start_block:,} - {end_block:,}, step {step}, {max_workers} workers",
            )

        last_save = 0
        buffered: dict[int, int] = {}
        for block_number, timestamp, task_rpc_request_stats in worker_processor(delayed(_read_timestamp_subprocess)(*args) for args in tasks):
            if rpc_request_stats is not None and task_rpc_request_stats is not None:
                rpc_request_stats.merge(task_rpc_request_stats)
            buffered[block_number] = timestamp
            if progress_bar is not None:
                progress_bar.update(1)
                progress_bar.set_postfix({"timestamp": timestamp})
            if block_number - last_save >= checkpoint_freq:
                timestamp_db.import_chain_data(chain_id, pd.Series(buffered))
                buffered.clear()
                last_save = block_number
        if buffered:
            timestamp_db.import_chain_data(chain_id, pd.Series(buffered))
        completed = True
        return timestamp_db.get_slicer()
    finally:
        if progress_bar is not None:
            progress_bar.close()
        if not completed:
            timestamp_db.close()


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
    rpc_request_stats: RPCRequestStats | None = None,
) -> BlockTimestampSlicer:
    """Fetch block timestamps, choose backend.

    - If Hypersync is available, use the optimised code path

    For arguments see :py:func:`fetch_block_timestamps_multiprocess`.

    :param step:
        Sampling interval. Wide intervals use a sparse, cache-aware Hypersync
        path instead of downloading every intervening block header.

    :return:
        Pandas series block number (int) -> block timestamp (datetime)

        The mapping is deliberately keyed by block number. Modern chains such
        as Monad can produce multiple blocks in one second, while block-header
        timestamps retain one-second precision. Do not reverse this mapping or
        treat timestamp values as unique; retaining the same timestamp for
        multiple blocks is correct for the scanner's intended precision.
    """

    if hypersync_client:
        from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached, fetch_sparse_block_timestamps_using_hypersync_cached

        if step >= HYPERSYNC_SPARSE_TIMESTAMP_MIN_STEP:
            return fetch_sparse_block_timestamps_using_hypersync_cached(
                client=hypersync_client,
                chain_id=chain_id,
                start_block=start_block,
                end_block=end_block,
                step=step,
                cache_path=cache_path,
                display_progress=display_progress,
            )

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
            rpc_request_stats=rpc_request_stats,
        )
