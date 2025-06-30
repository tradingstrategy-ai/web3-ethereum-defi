"""Web3 connection factory.

Methods for creating Web3 connections over multiple threads and processes.
"""

from collections import Counter
from threading import local
from typing import Protocol, Optional, Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware, install_retry_middleware, install_api_call_counter_middleware
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.provider.multi_provider import create_multi_provider_web3

_web3_thread_local_cache = local()


class Web3Factory(Protocol):
    """Create a new Web3 connection.

    - Web3 connection cannot be passed across thread/process boundaries

    - Help to setup TCP/IP connections and Web3 instance over it in threads and processes

    - When each worker is initialised, the factory is called to get JSON-RPC connection

    `See Python documentation regarding typing.Protocol <https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs>`__.
    """

    def __call__(self, context: Optional[Any] = None) -> Web3:
        """Create a new Web3 connection.

        :param context:
            Any context arguments a special factory might need.

        :return:
            New Web3 connection
        """


class TunedWeb3Factory(Web3Factory):
    """Create a Web3 connections.

    A factory that allows us to pass web3 connection creation method
    across thread and process bounderies.

    - Disable AttributedDict middleware and other middleware that slows us down

    - Enable graceful retries in the case of network errors and API throttling

    - Use faster `ujson` instead of stdlib json to decode the responses
    """

    def __init__(
        self,
        rpc_config_line: str,
        http_adapter: Optional[HTTPAdapter] = None,
        thread_local_cache=False,
        api_counter=False,
    ):
        """Set up a factory.

        :param rpc_config_line:
            JSON-RPC config line.

            See :py:mod:`eth_defi.provider.multi_provider`.

        :param http_adapter:
            Connection pooling for HTTPS.

            Parameters for `requests` library.
            Default to pool size 10.

        :param thread_local_cache:
            Construct the web3 connection only once per thread.

            If you are using thread pooling, recycles the connection
            across different factory calls.

        :param api_counter:
            Enable API counters

        """
        self.rpc_config_line = rpc_config_line

        if not http_adapter:
            http_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)

        self.http_adapter = http_adapter
        self.thread_local_cache = thread_local_cache

        if api_counter:
            assert self.thread_local_cache, "You must use thread locals with API counters for now"
            self.api_counters: List[Counter] = []
        else:
            self.api_counters = None

    def __call__(self, context: Optional[Any] = None) -> Web3:
        """Create a new Web3 connection.

        - Get rid of middleware

        - Patch for ujson
        """

        if self.thread_local_cache:
            web3 = getattr(_web3_thread_local_cache, "web3", None)
            if web3 is not None:
                return web3

        # Reuse HTTPS session for HTTP 1.1 keep-alive
        session = requests.Session()
        session.mount("https://", self.http_adapter)

        web3 = create_multi_provider_web3(self.rpc_config_line, session=session)

        if self.thread_local_cache:
            _web3_thread_local_cache.web3 = web3

        if self.api_counters is not None:
            counter = install_api_call_counter_middleware(web3)
            self.api_counters.append(counter)

        return web3

    def get_total_api_call_counts(self) -> Counter:
        """Sum API call counts across all threads"""
        assert len(self.api_counters) > 0, "No API count enabled"
        # https://stackoverflow.com/a/37337341/315168
        return sum(self.api_counters, Counter())


class SimpleWeb3Factory:
    """Single reusable Web3 connection.

    - Does not work for multithreaded use cases, because Web3 object
      with TCP/IP connection is not passable across thread or process boundaries

    - Useful for testing
    """

    def __init__(self, web3: Web3):
        self.web3 = web3

    def __call__(self, context: Optional[Any] = None) -> Web3:
        return self.web3
