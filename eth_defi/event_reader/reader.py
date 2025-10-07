"""High performance EVM event reader.

For further reading see:

- `Ethereum JSON-RPC API spec <https://playground.open-rpc.org/?schemaUrl=https://raw.githubusercontent.com/ethereum/execution-apis/assembled-spec/openrpc.json&uiSchema%5BappBar%5D%5Bui:splitView%5D=false&uiSchema%5BappBar%5D%5Bui:input%5D=false&uiSchema%5BappBar%5D%5Bui:examplesDropdown%5D=false>`_

- `futureproof - - Bulletproof concurrent.futures for Python <https://github.com/yeraydiazdiaz/futureproof>`_

"""

import logging
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Set, Any, TypeAlias, Union

import futureproof
from eth_bloom import BloomFilter
from futureproof import ThreadPoolExecutor
from hexbytes import HexBytes
from requests.adapters import HTTPAdapter
from web3 import Web3
from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.logresult import LogContext, LogResult
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import get_worker_web3, create_thread_pool_executor
from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.middleware import is_retryable_http_exception

logger = logging.getLogger(__name__)


class TimestampNotFound(Exception):
    """Timestamp service does not have a timestasmp for a given block."""


class BadTimestampValueReturned(Exception):
    """Timestamp does not look good."""


class ReadingLogsFailed(Exception):
    """eth_getLogs call failed."""


#: How to pass a connection to the event readers
#:
#: - Single-threaded readers take Web3 instance as is, because this is the simplest
#:
#: - Multithreaded readers set up their own connection pools behind the scenes,
#:   and passing a single connection around is not meaningful
#:
#:
ReaderConnection: TypeAlias = Union[Web3, None]


# For typing.Protocol see https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs
class ProgressUpdate(Protocol):
    """Informs any listener about the state of an event scan.

    Called before a new block is processed.

    Hook this up with `tqdm` for an interactive progress bar.
    """

    def __call__(
        self,
        current_block: int,
        start_block: int,
        end_block: int,
        chunk_size: int,
        total_events: int,
        last_timestamp: Optional[int],
        context: LogContext,
    ):
        """
        :param current_block:
            The block we are about to scan.
            After this scan, we have scanned `current_block + chunk_size` blocks

        :param start_block:
            The first block in our total scan range.

        :param end_block:
            The last block in our total scan range.

        :param chunk_size:
            What was the chunk size (can differ for the last scanned chunk)

        :param total_events:
            Total events picked up so far

        :param last_timestamp:
            UNIX timestamp of last picked up event (if any events picked up)

        :param context:
            Current context
        """


# For typing.Protocol see https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs
class Web3EventReader(Protocol):
    """Pass the event reader callable around.

    An interface over event reader iterator.

    - Helps to type decorate event reader callable in the function arguments

    - The event reader implementation may be single-threaded, multithreaded, async based, etc.

    For concrete implementation see

    - :py:func:`read_events`

    - :py:func:`read_events_concurrent`

    - :py:func:`extract_events`

    - :py:func:`extract_events_concurrent`

    Example by using a single-thread reader:

    .. code-block:: python

        from eth_defi.event_reader.reader import extract_events, Web3EventReader
        from eth_defi.enzyme.events import fetch_vault_balance_events

        read_events: Web3EventReader = cast(Web3EventReader, partial(extract_events))
        balance_events = list(fetch_vault_balance_events(vault, start_block, end_block, read_events))
    """

    def __call__(
        self,
        web3: ReaderConnection,
        start_block: int,
        end_block: int,
        filter: Filter,
    ):
        """Read events for a block range.

        :param web3:
            Web3 instance for single-threaded readers

        :param start_block:
            First block to process (inclusive)

        :param end_block:
            Last block to process (inclusive)

        :param filter:
            Internal filter used to match logs.

        :param end_block:
            The last block in our total scan range.

        :raise ChainReorganisationDetected:
            If used with chain reorganisation monitor, detect any changes in the underlying chain state.

            See :py:mod:`eth_defi.reader.reorganisation_monitor` for details.
        """


def extract_timestamps_json_rpc(
    web3: Web3,
    start_block: int,
    end_block: int,
) -> Dict[str, int]:
    """Get block timestamps from block headers.

    Use slow JSON-RPC block headers call to get this information.

    TODO: This is an old code path. This has been replaced by more robust
    :py:class:`ReorganisationMonitor` implementation.

    :return:
        block hash -> UNIX timestamp mapping
    """
    timestamps = {}

    logging.debug("Extracting timestamps for logs %d - %d", start_block, end_block)

    # Collect block timestamps from the headers
    for block_num in range(start_block, end_block + 1):
        raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
        data_block_number = convert_jsonrpc_value_to_int(raw_result["number"])
        assert data_block_number == block_num, "Blockchain node did not give us the block we want"
        timestamps[raw_result["hash"]] = convert_jsonrpc_value_to_int(raw_result["timestamp"])

    return timestamps


def extract_events(
    web3: Web3,
    start_block: int,
    end_block: int,
    filter: Filter,
    context: Optional[LogContext] = None,
    extract_timestamps: Optional[Callable] = extract_timestamps_json_rpc,
    reorg_mon: Optional[ReorganisationMonitor] = None,
    attempts=5,
    throttle_sleep=15,
) -> Iterable[LogResult]:
    """Perform eth_getLogs call over a block range.

    You should use :py:func:`read_events` unless you know the block range is something your node can handle.

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param filter:
        Internal filter used to match logs

    :param extract_timestamps:
        Method to get the block timestamps.

        This might need to use expensive`eth_getBlockByNumber` JSON-RPC API call.
        It will seriously slow down event reading.
        Set `extract_timestamps` to `None` to not get timestamps, but fast event lookups.


    :param context:
        Passed to the all generated logs

    :param reorg_mon:
        If passed, use this instance to monitor and raise chain reorganisation exceptions.

    :return:
        Iterable for the raw event data
    """

    if reorg_mon:
        assert extract_timestamps is None, "You cannot pass both reorg_mon and extract_timestamps"

    topics = list(filter.topics.keys())

    # https://www.quicknode.com/docs/ethereum/eth_getLogs
    # https://docs.alchemy.com/alchemy/guides/eth_getlogs
    filter_params = {
        "topics": [topics],  # JSON-RPC has totally braindead API to say how to do OR event lookup
        "fromBlock": hex(start_block),
        "toBlock": hex(end_block),
    }

    # Do the filtering by address.
    # eth_getLogs gets single address or JSON list of addresses
    if filter.contract_address:
        assert type(filter.contract_address) in (list, str), f"Got: {type(filter.contract_address)}"
        filter_params["address"] = filter.contract_address

    # logging.debug("Extracting logs %s", filter_params)
    # logging.info("Log range %d - %d", start_block, end_block)

    try:
        # Bypass all middleware so it does not slow us down
        for attempt in range(attempts, 0, -1):
            try:
                logs = web3.manager.request_blocking("eth_getLogs", (filter_params,))
                break
            except Exception as e:
                if is_retryable_http_exception(e) and attempt > 0:
                    logger.warning(
                        "Throttling extract_events(): %s, sleeping %s",
                        e,
                        throttle_sleep,
                    )
                    time.sleep(throttle_sleep)
                    continue
                else:
                    raise

    except Exception as e:
        block_count = end_block - start_block
        raise ReadingLogsFailed(f"eth_getLogs failed for {start_block:,} - {end_block:,} (total {block_count:,} with filter {filter}") from e

    if logs:
        if extract_timestamps is not None:
            timestamps = extract_timestamps(web3, start_block, end_block)
            if timestamps is None:
                raise BadTimestampValueReturned("extract_timestamps returned None")
        else:
            timestamps = None

        for log in logs:
            block_hash = log["blockHash"]
            block_number = convert_jsonrpc_value_to_int(log["blockNumber"])
            # Retrofit our information to the dict
            event_signature = log["topics"][0]

            if isinstance(log, AttributeDict):
                # The following code is not going to work, because AttributeDict magic
                raise RuntimeError("web3.py AttributeDict middleware detected. Please remove it with web3.middleware_onion.remove('attrdict') or from web3.provider.middleware list before attempting to read events")

            if isinstance(log["data"], HexBytes):
                raise RuntimeError("web3.py pythonic middleware detected. Please remove it with web3.middleware_onion.remove('pythonic') before attempting to read events")

            log["context"] = context

            if type(event_signature) == HexBytes:
                # Make sure we use lowercase string notation everywhere
                event_signature = event_signature.hex()

            log["event"] = filter.topics[event_signature]

            # Can be hex string or integer (EthereumTester)
            log["blockNumber"] = convert_jsonrpc_value_to_int(log["blockNumber"])

            # Used for debugging if we are getting bad data from node
            # or internally confused
            log["chunk_id"] = start_block

            if reorg_mon:
                # Raises exception if chain tip has changed
                timestamp = reorg_mon.check_block_reorg(block_number, block_hash)
                assert timestamp is not None, f"Timestamp missing for block number {block_number}, hash {block_hash}. reorg known last block is: {reorg_mon.get_last_block_read()}"
                log["timestamp"] = timestamp
            else:
                if timestamps is not None:
                    try:
                        log["timestamp"] = timestamps[block_hash]
                        if type(log["timestamp"]) not in (int, float):
                            raise BadTimestampValueReturned(f"Timestamp was not int or float: {type(log['timestamp'])}: {type(log['timestamp'])}")
                    except KeyError as e:
                        # Reorg mon would handle this natively
                        raise TimestampNotFound(f"EVM event reader cannot match timestamp.\nTimestamp missing for block number {block_number:,}, hash {block_hash}.\n our timestamp table has {len(timestamps)} blocks.") from e
                else:
                    # Not set, because reorg mon and timestamp extractor not provided,
                    # the caller must do the timestamp resolution themselves
                    log["timestamp"] = None

            yield log


def extract_events_concurrent(
    start_block: int,
    end_block: int,
    filter: Filter,
    context: Optional[LogContext] = None,
    extract_timestamps: Optional[Callable] = extract_timestamps_json_rpc,
) -> List[LogResult]:
    """Concurrency happy event extractor.

    You should use :py:func:`read_events_concurrent` unless you know the block range is something your node can handle.

    Called by the thread pool - you probably do not want to call this directly.

    Assumes the web3 connection is preset when the concurrent worker has been created,
    see `get_worker_web3()`.
    """
    logger.debug("Starting block scan %d - %d at thread %d for %d different events", start_block, end_block, threading.get_ident(), len(filter.topics))
    web3 = get_worker_web3()
    assert web3 is not None
    events = list(extract_events(web3, start_block, end_block, filter, context, extract_timestamps))
    return events


def prepare_filter(events: List[ContractEvent]) -> Filter:
    """Creates internal filter to match contract events."""

    # Construct our bloom filter
    bloom = BloomFilter()
    topics = {}

    for event in events:
        signatures = event.build_filter().topics

        for signature in signatures:
            topics[signature] = event
            # TODO: Confirm correct usage of bloom filter for topics
            bloom.add(bytes.fromhex(signature[2:]))

    filter = Filter(topics, bloom)

    return filter


def read_events(
    web3: Web3,
    start_block: int,
    end_block: int,
    events: Optional[List[ContractEvent]] = None,
    notify: Optional[ProgressUpdate] = None,
    chunk_size: int = 100,
    context: Optional[LogContext] = None,
    extract_timestamps: Optional[Callable] = extract_timestamps_json_rpc,
    filter: Optional[Filter] = None,
    reorg_mon: Optional[ReorganisationMonitor] = None,
) -> Iterable[LogResult]:
    """Reads multiple events from the blockchain.

    Optimized to read multiple events from test blockchains.

    .. note ::

        For a much faster event reader check :py:class:`eth_defi.reader.multithread.MultithreadEventReader`.
        This implementation is mostly good with EVM test backends or very small block ranges.

    - Scans chains block by block

    - Returns events as a dict for optimal performance

    - Supports interactive progress bar

    - Reads all the events matching signature - any filtering must be done
      by the reader

    See `scripts/read-uniswap-v2-pairs-and-swaps.py` for a full example.

    Example:

    .. code-block:: python

        json_rpc_url = os.environ["JSON_RPC_URL"]
        web3 = Web3(HTTPProvider(json_rpc_url)

        web3.middleware_onion.clear()

        # Get contracts
        Factory = get_contract(web3, "sushi/UniswapV2Factory.json")

        start_block = 1
        end_block = web3.eth.block_number

        filter = Filter.create_filter(
            factory_address,
            [Factory.events.PairCreated],
        )

        # Read through all the events, all the chain, using a single threaded slow loop.
        # Only suitable for test EVM backends.
        pairs = []
        log: LogResult
        for log in read_events(
            web3,
            start_block,
            end_block,
            filter=filter,
            extract_timestamps=None,
        ):
            # Signature this
            #
            #  event PairCreated(address indexed token0, address indexed token1, address pair, uint);
            #
            # topic 0 = keccak(event signature)
            # topic 1 = token 0
            # topic 2 = token 1
            # argument 0 = pair
            # argument 1 = pair id
            #
            # log for EthereumTester backend is
            #
            # {'type': 'mined',
            #  'logIndex': 0,
            #  'transactionIndex': 0,
            #  'transactionHash': HexBytes('0x2cf4563f8c275e5b5d7a4e5496bfbaf15cc00d530f15f730ac4a0decbc01d963'),
            #  'blockHash': HexBytes('0x7c0c6363bc8f4eac452a37e45248a720ff09f330117cdfac67640d31d140dc38'),
            #  'blockNumber': 6,
            #  'address': '0xF2E246BB76DF876Cef8b38ae84130F4F55De395b',
            #  'data': HexBytes('0x00000000000000000000000068931307edcb44c3389c507dab8d5d64d242e58f0000000000000000000000000000000000000000000000000000000000000001'),
            #  'topics': [HexBytes('0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9'),
            #   HexBytes('0x0000000000000000000000002946259e0334f33a064106302415ad3391bed384'),
            #   HexBytes('0x000000000000000000000000b9816fc57977d5a786e654c7cf76767be63b966e')],
            #  'context': None,
            #  'event': web3._utils.datatypes.PairCreated,
            #  'chunk_id': 1,
            #  'timestamp': None}
            #
            arguments = decode_data(log["data"])
            topics = log["topics"]
            token0 = convert_uint256_hex_string_to_address(topics[1])
            token1 = convert_uint256_hex_string_to_address(topics[2])
            pair_address = convert_uint256_bytes_to_address(arguments[0])
            pair_id = convert_int256_bytes_to_int(arguments[1])

            token0_details = fetch_erc20_details(web3, token0)
            token1_details = fetch_erc20_details(web3, token1)


    :param web3:
        Web3 instance

    :param events:
        List of Web3.py contract event classes to scan for.

        Pass this or filter.

    :param notify:
        Optional callback to be called before starting to scan each chunk

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param extract_timestamps:
        Override for different block timestamp extraction methods.

        This might need to use expensive`eth_getBlockByNumber` JSON-RPC API call.
        It will seriously slow down event reading.
        Set `extract_timestamps` to `None` to not get timestamps, but fast event lookups.

    :param chunk_size:
        How many blocks to scan in one eth_getLogs call

    :param context:
        Passed to the all generated logs

    :param filter:
        Pass a custom event filter for the readers

        Pass this or events.

    :param reorg_mon:
        If passed, use this instance to monitor and raise chain reorganisation exceptions.

    :return:
        Iterate over :py:class:`LogResult` instances for each event matched in
        the filter.
    """

    assert type(start_block) == int
    assert type(end_block) == int

    total_events = 0

    # TODO: retry middleware makes an exception
    # assert len(web3.middleware_onion) == 0, f"Must not have any Web3 middleware installed to slow down scan, has {web3.middleware_onion.middlewares}"

    # Construct our bloom filter
    if filter is None:
        assert events is not None, "Cannot pass both filter and events"
        filter = prepare_filter(events)

    last_timestamp = None

    for block_num in range(start_block, end_block + 1, chunk_size):
        last_of_chunk = min(end_block, block_num + chunk_size - 1)

        logger.debug("Extracting eth_getLogs from %d - %d", block_num, last_of_chunk)

        batch_events = 0

        # Stream the events
        for event in extract_events(
            web3,
            block_num,
            last_of_chunk,
            filter,
            context,
            extract_timestamps,
            reorg_mon,
        ):
            last_timestamp = event.get("timestamp")
            total_events += 1
            batch_events += 1
            yield event

        # Ping our master,
        # only when we have an event hit not to cause unnecessary block header fetches
        # TODO: Add argument notify always
        if notify is not None and batch_events:
            notify(block_num, start_block, end_block, chunk_size, total_events, last_timestamp, context)


def read_events_concurrent(
    executor: ThreadPoolExecutor,
    start_block: int,
    end_block: int,
    events: Optional[List[ContractEvent]] = None,
    notify: Optional[ProgressUpdate] = None,
    chunk_size: int = 100,
    context: Optional[LogContext] = None,
    extract_timestamps: Optional[Callable] = extract_timestamps_json_rpc,
    filter: Optional[Filter] = None,
    reorg_mon: Optional[ReorganisationMonitor] = None,
) -> Iterable[LogResult]:
    """Reads multiple events from the blockchain parallel using a thread pool for IO.

    Optimized to read multiple events fast.

    - Uses a thread worker pool for concurrency

    - Even though we receive data from JSON-RPC API in random order,
      the iterable results are always in the correct order (and processes in a single thread)

    - Returns events as a dict for optimal performance

    - Can resume scan

    - Supports interactive progress bar

    - Reads all the events matching signature - any filtering must be done
      by the reader

    See `scripts/read-uniswap-v2-pairs-and-swaps-concurrent.py` for a full example.

    Example:

    .. code-block:: python

        json_rpc_url = os.environ["JSON_RPC_URL"]
        token_cache = TokenCache()
        threads = 16
        http_adapter = requests.adapters.HTTPAdapter(pool_connections=threads, pool_maxsize=threads)
        web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter)
        web3 = web3_factory(token_cache)
        executor = create_thread_pool_executor(web3_factory, context=token_cache, max_workers=threads)

        # Get contracts
        Factory = get_contract(web3, "UniswapV2Factory.json")

        events = [
            Factory.events.PairCreated,
        ]

        start_block = 10_000_835  # Uni deployed
        end_block = 10_009_000  # The first pair created before this block

        # Read through the blog ran
        out = []
        for log_result in read_events_concurrent(
            executor,
            start_block,
            end_block,
            events,
            None,
            chunk_size=100,
            context=token_cache,
            extract_timestamps=None,
        ):
            out.append(decode_pair_created(log_result))

    :param executor:
        Thread pool executor created with :py:func:`eth_defi.event_reader.web3worker.create_thread_pool_executor`

    :param events:
        List of Web3.py contract event classes to scan for

    :param notify:
        Optional callback to be called before starting to scan each chunk

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param extract_timestamps:
        Override for different block timestamp extraction methods

    :param chunk_size:
        How many blocks to scan in one eth_getLogs call

    :param context:
        Passed to the all generated logs

    :param filter:
        Pass a custom event filter for the readers

    :param reorg_mon:
        If passed, use this instance to monitor and raise chain reorganisation exceptions.

    :return:
        Iterate over :py:class:`LogResult` instances for each event matched in
        the filter.

    """

    assert not executor._executor._shutdown, "ThreadPoolExecutor has been shut down"

    total_events = 0

    last_timestamp = None

    if extract_timestamps:
        assert not reorg_mon, "Pass either extract_timestamps or reorg_mon"

    # Construct our bloom filter
    if filter is None:
        assert events is not None, "Cannot pass both filter and events"
        filter = prepare_filter(events)

    # For futureproof usage see
    # https://github.com/yeraydiazdiaz/futureproof
    tm = futureproof.TaskManager(executor, error_policy=futureproof.ErrorPolicyEnum.RAISE)

    # Build a list of all tasks
    # block number -> arguments
    task_list: Dict[int, tuple] = {}
    completed_tasks: Dict[int, tuple] = {}

    for block_num in range(start_block, end_block + 1, chunk_size):
        last_of_chunk = min(end_block, block_num + chunk_size - 1)
        task_list[block_num] = (
            block_num,
            last_of_chunk,
            filter,
            context,
            extract_timestamps,
        )

    # Run all tasks and handle backpressure. Task manager
    # will execute tasks as soon as there is room in the worker pool.
    tm.map(extract_events_concurrent, list(task_list.values()))

    logger.debug("Submitted %d tasks", len(task_list))

    processed_chunks: Set[int] = set()

    # Complete the tasks.
    # Always guarantee the block order for the caller,
    # so that events are iterated in the correct order
    for task in tm.as_completed():
        block_num = task.args[0]  # Peekaboo
        completed_tasks[block_num] = task
        logger.debug("Completed block range at block %d", block_num)

        # Iterate through the start for the task list
        # and then yield the completed blocks forward
        tasks_pending = list(task_list.keys())  # By a block number

        # Iterate through the tasks in their correct order
        for block_num in tasks_pending:
            # The first task at the head of pending list is complete
            if block_num in completed_tasks:
                # Remove task from our list
                task = completed_tasks.pop(block_num)
                del task_list[block_num]

                if isinstance(task.result, Exception):
                    raise AssertionError("Should not never happen")

                assert task.result is not None, f"Result missing for the task: {task}"

                log_results: List[LogResult] = task.result

                chunk_id = task.args[0]  # start_block
                logger.debug("Completed chunk: %d, got %d results", chunk_id, len(log_results))

                assert chunk_id not in processed_chunks, f"Duplicate chunk: {chunk_id}"
                processed_chunks.add(chunk_id)

                # Too naisy
                # logger.debug("Result is %s", log_results)

                # Pass through the logs and do timestamp resolution for them
                #
                batch_events = 0
                for log in log_results:
                    # Check that this event is not from an alternative chain tip
                    if reorg_mon:
                        timestamp = reorg_mon.check_block_reorg(
                            convert_jsonrpc_value_to_int(log["blockNumber"]),
                            log["blockHash"],
                        )
                        last_timestamp = log["timestamp"] = timestamp
                    else:
                        # Assume extracted with extract_timestamps_json_rpc
                        last_timestamp = log.get("timestamp")

                    yield log
                    total_events += 1
                    batch_events += 1

                # Only notify our progress bar if we have an enenet hit
                # to avoid unnecessary timestamp reads
                if notify is not None and batch_events:
                    notify(block_num, start_block, end_block, chunk_size, total_events, last_timestamp, context)

            else:
                # This task is not yet completed,
                # but block ranges after this task are.
                # Because we need to return events in their order,
                # we try to return events from this completed tasks later,
                # when we have some results from earlier tasks first.
                break
