"""Multithreaded and parallel Solidity event reading helpers."""
from typing import Any, Optional, List, Iterable

from requests.adapters import HTTPAdapter
from web3.contract.contract import ContractEvent

from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.logresult import LogResult
from eth_defi.event_reader.reader import Web3EventReader, read_events_concurrent, ReaderConnection, ProgressUpdate
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor


class MultithreadEventReader(Web3EventReader):
    """Multithreaded and parallel Solidity event reading.

    - This class wraps around lower level event reading functions to
      simpler to use and well-documented example.

    - It is designed to be used standalone (you read the events)

    - It is designed to be passed to the functions that expect :py:class:`Web3EventReader` protocol

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

        """
        self.http_adapter = HTTPAdapter(pool_connections=max_threads, pool_maxsize=max_threads)
        self.web3_factory = TunedWeb3Factory(json_rpc_url, self.http_adapter, thread_local_cache=True, api_counter=api_counter)
        self.executor = create_thread_pool_executor(self.web3_factory, reader_context, max_workers=max_threads)
        self.max_blocks_once = max_blocks_once
        self.reader_context = reader_context
        self.reorg_mon = reorg_mon
        self.notify = notify

        # Set up the timestamp reading method

    def close(self):
        """Release the allocated resources."""
        self.executor.join()
        self.http_adapter.close()

    def __call__(self,
        web3: ReaderConnection,
        start_block: int,
        end_block: int,
        events: Optional[List[ContractEvent]] = None,
        filter: Optional[Filter] = None,
        ) -> Iterable[LogResult]:
            """Wrap the underlying low-level function.

            Wrap :py:func:`eth_defi.reader.reader.read_events_concurrent` using worker pools we have set up.

            .. note ::

                Currently timestamp reading not supported

            """
            yield from read_events_concurrent(
                self.executor,
                start_block,
                end_block,
                events=events,
                filter=filter,
                reorg_mon=self.reorg_mon,
                notify=self.notify,
                extract_timestamps=None,
            )
