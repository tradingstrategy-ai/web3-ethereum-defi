"""Multithreaded and parallel Solidity event reading helpers."""
from typing import Any, Optional, List, Iterable, Counter, Callable

from requests.adapters import HTTPAdapter
from web3.contract.contract import ContractEvent

from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.logresult import LogResult
from eth_defi.event_reader.reader import Web3EventReader, read_events_concurrent, ReaderConnection, ProgressUpdate, read_events
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor


class MultithreadEventReader(Web3EventReader):
    """Multithreaded and parallel Solidity event reading.

    - A high performance event reader for JSON-RPC APIs

    - Uses parallel requests, but the consumer will always get the events in the order
      they have happened on-chain

    - This class wraps around lower level event reading functions to
      simpler to use and well-documented example.

    - It is designed to be used standalone (you read the events)

    - It is designed to be passed to the functions that expect :py:class:`Web3EventReader` protocol

    See :ref:`multithread-reader` for full tutorial.

    Example how to read events:

    .. code-block:: python

        # Get one of the contracts prepackaged ABIs from eth_defi package
        value_interpreter_contract = get_contract(web3, "enzyme/ValueInterpreter.json")

        # Read events only for this contract
        # See https://docs.enzyme.finance/developers/contracts/polygon
        target_contract_address = "0x66De7e286Aae66f7f3Daf693c22d16EEa48a0f45"

        # Create eth_getLogs event filtering
        filter = Filter.create_filter(
            target_contract_address,
            [value_interpreter_contract.events.PrimitiveAdded],
        )

        # Set up multithreaded Polygon event reader.
        # Print progress to the console how many blocks there are left to read.
        reader = MultithreadEventReader(
            json_rpc_url,
            max_threads=16,
            notify=PrintProgressUpdate(),
            max_blocks_once=10_000)

        # Loop over the events as the multihreaded reader pool is feeding them to us.
        # Events will always arrive in the order they happened on chain.
        decoded_events = []
        start = datetime.datetime.utcnow()
        for event in reader(
            web3,
            start_block,
            end_block,
            filter=filter,
        ):
            # Decode the solidity event
            #
            # Indexed event parameters go to EVM topics, the second element is the first parameter
            # Non-indexed event parameters go to EVM arguments, first element is the first parameter
            arguments = decode_data(event["data"])
            topics = event["topics"]

            # event PrimitiveAdded(
            #     address indexed primitive,
            #     address aggregator,
            #     RateAsset rateAsset,
            #     uint256 unit
            # );
            primitive = convert_uint256_bytes_to_address(HexBytes(topics[1]))
            aggregator = convert_uint256_bytes_to_address(arguments[0])
            rate_asset = convert_int256_bytes_to_int(arguments[1])
            unit = convert_int256_bytes_to_int(arguments[2])

            # Primitive is a ERC-20 token, resolve its name and symbol while we are decoded the events
            token = fetch_erc20_details(web3, primitive)

            decoded = {
                "primitive": primitive,
                "aggregator": aggregator,
                "rate_asset": rate_asset,
                "unit": unit,
                "token": token,
            }

    Example how to pass the multithread reader to a function consuming events:

    .. code-block:: python

        provider = cast(HTTPProvider, web3.provider)
        json_rpc_url = provider.endpoint_uri
        reader = MultithreadEventReader(json_rpc_url, max_threads=16)

        start_block = 1
        end_block = web3.eth.block_number

        # Iterate over all price feed added events
        # over the whole blockchain range
        feed_iter = fetch_price_feeds(
            deployment,
            start_block,
            end_block,
            reader,
        )
        feeds = list(feed_iter)
        reader.close()
        assert len(feeds) == 2

    Because Ethereum does not have native JSON-RPC API to get block timestamps and headers
    easily, there are many work arounds how to get timestamps for events.
    Here is an example how to fetch timestamps "lazily" only for blocks
    where you have events:

    .. code-block:: python

        from eth_defi.event_reader.lazy_timestamp_reader import extract_timestamps_json_rpc_lazy

        provider = cast(HTTPProvider, web3.provider)
        json_rpc_url = provider.endpoint_uri
        reader = MultithreadEventReader(json_rpc_url, max_threads=16)

        start_block = 1
        end_block = web3.eth.block_number

        reader = MultithreadEventReader(
            json_rpc_url,
        )

        for log_result in reader(
            web3,
            restored_start_block,
            end_block,
            filter=filter,
            extract_timestamps=extract_timestamps_json_rpc_lazy,
        ):
            pass

    See :py:func:`eth_defi.event_reader.lazy_timestamp_reader.extract_timestamps_json_rpc_lazy`
    and :py:func:`eth_defi.uniswap_v3.events.fetch_events_to_csv` for more details.
    """

    def __init__(
            self,
            json_rpc_url: str,
            max_threads=10,
            reader_context: Any = None,
            api_counter=True,
            max_blocks_once=50_000,
            reorg_mon: Optional[ReorganisationMonitor] = None,
            notify: Optional[ProgressUpdate] = None,
            auto_close_notify=True,
    ):
        """Creates a multithreaded JSON-RPC reader pool.

        Can be passed to any function that expects :py:class:`Web3EventReader` protocol.

        - Sets up `requests` session pool for `max_threads` threads

        - Sets up `futureproof` thread pool executors for `max_threads` threads

        :param json_rpc_url:
            Your node URL

        :param max_threads:
            How many threads to allocate

        :param reader_context:
            Passed to the reader callback

        :param api_counter:
            Enable cross-thread API counter

        :param notify:
            Notify interface for progress reports.

        :param max_blocks_once:
            How many blocks your node's eth_getLogs call can serve.

            Crappy node providers set this value very low, around 1000,
            slowing down the reading.

        :param reorg_mon:
           Chain reorganisation monitor.

           The policy class for dealing with chain tip changes during the read or between event reads.

           If you do not want block hashes and timestamps for the events, or you do not want to check
           for potential reorganisations, you can set this to `None`.

        :param auto_close_notify:
            Close the notifier after the operation is done.

            Assume notifier object has close() method.

        """
        self.http_adapter = HTTPAdapter(pool_connections=max_threads, pool_maxsize=max_threads)
        self.web3_factory = TunedWeb3Factory(json_rpc_url, self.http_adapter, thread_local_cache=True, api_counter=api_counter)
        self.executor = create_thread_pool_executor(self.web3_factory, reader_context, max_workers=max_threads)
        self.max_blocks_once = max_blocks_once
        self.reader_context = reader_context
        self.reorg_mon = reorg_mon
        self.notify = notify
        self.auto_close_notify = auto_close_notify

        # Set up the timestamp reading method

    def get_max_threads(self) -> int:
        return self.executor.max_workers

    def close(self):
        """Release the allocated resources."""
        self.executor.join()
        self.http_adapter.close()

        if self.auto_close_notify:
            if hasattr(self.notify, "close"):
                self.notify.close()

    def __call__(
            self,
            web3: ReaderConnection,
            start_block: int,
            end_block: int,
            events: Optional[List[ContractEvent]] = None,
            filter: Optional[Filter] = None,
            extract_timestamps: Optional[Callable] = None,
    ) -> Iterable[LogResult]:
        """Wrap the underlying low-level function.

        Wrap :py:func:`eth_defi.reader.reader.read_events_concurrent` using worker pools we have set up.

        .. note ::

            Currently timestamp reading not supported

        :param web3:
            Currently unused

        :param start_block:
            First block to call in eth_getLogs. Inclusive.

        :param end_block:
            End block to call in eth_getLogs. Inclusive.

        :param events:
            Event signatures we are interested in.

            Legacy. Use ``filter`` instead.

        :param filter:
            Event filter we are using.

        :param extract_timestamps:
            Use this method to get timestamps for our events.

            Overrides :py:attr:`reorg_mon` given in the constructor (if any given).
            See usage examples in :py:class:`MultithreadEventReader`.

        :return:
            Iterator for the events in the order they were written in the chain

        """

        if self.get_max_threads() == 1:
            # Single thread debug mode
            web3 = self.web3_factory()
            yield from read_events(
                web3,
                start_block,
                end_block,
                events=events,
                filter=filter,
                reorg_mon=self.reorg_mon,
                notify=self.notify,
                extract_timestamps=extract_timestamps,
                chunk_size=self.max_blocks_once,
            )

        else:
            # Multi thread production mode
            yield from read_events_concurrent(
                self.executor,
                start_block,
                end_block,
                events=events,
                filter=filter,
                reorg_mon=self.reorg_mon,
                notify=self.notify,
                extract_timestamps=extract_timestamps,
                chunk_size=self.max_blocks_once,
            )

    def get_total_api_call_counts(self) -> Counter:
        """Sum API call counts across all threads.

        See :py:func:`eth_defi.chain.install_api_call_counter_middleware` for details.
        """
        return self.web3_factory.get_total_api_call_counts()
