"""Web3 connection factory."""

# For typing.Protocol see https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs
from typing import Protocol, Optional

import requests
from requests.adapters import HTTPAdapter
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.logresult import LogContext
from eth_defi.middleware import http_retry_request_with_sleep_middleware


class Web3Factory(Protocol):
    """Create a new Web3 connection.

    When each worker is initialised, the factory is called to get JSON-RPC connection.
    """

    def __call__(self, context: LogContext) -> Web3:
        pass


class TunedWeb3Factory(Web3Factory):
    """Create a Web3 connections.

    A factory that allows us to pass web3 connection creation method
    across thread and process bounderies.
    """

    def __init__(self, json_rpc_url: str, http_adapter: Optional[HTTPAdapter] = None):
        """Set up a factory.

        :param json_rpc_url:
            Node JSON-RPC server URL.

        :param http_adapter:
            Connection pooling for HTTPS.

            Parameters for `requests` library.
            Default to pool size 10.

        """
        self.json_rpc_url = json_rpc_url

        if not http_adapter:
            http_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)

        self.http_adapter = http_adapter

    def __call__(self, context: LogContext) -> Web3:
        """Create a new Web3 connection.

        - Get rid of middleware

        - Patch for ujson
        """

        # Reuse HTTPS session for HTTP 1.1 keep-alive
        session = requests.Session()
        session.mount("https://", self.http_adapter)

        web3 = Web3(HTTPProvider(self.json_rpc_url, session=session))

        # Enable faster ujson reads
        patch_web3(web3)

        web3.middleware_onion.clear()
        install_chain_middleware(web3)
        install_retry_middleware(web3)

        return web3


class SimpleWeb3Factory:
    """Single reusable Web3 connection.

    - Does not work for multithreaded use cases, because Web3 object
      with TCP/IP connection is not passable across thread or process boundaries

    - Useful for testing
    """

    def __init__(self, web3: Web3):
        self.web3 = web3

    def __call__(self, context: LogContext) -> Web3:
        return self.web3
