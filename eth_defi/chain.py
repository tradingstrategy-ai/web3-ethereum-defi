"""Chain specific configuration.

Many chains like Polygon and BNB Chain may need their own Web3 connection tuning.
In this module, we have helpers.
"""
import datetime
from collections import Counter
from typing import Callable, Optional, Any

#: These chains need POA middleware
from urllib.parse import urljoin

import requests
from web3 import Web3, HTTPProvider
from web3.middleware import geth_poa_middleware
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.middleware import http_retry_request_with_sleep_middleware

#: List of chain ids that need to have proof-of-authority middleweare installed
POA_MIDDLEWARE_NEEDED_CHAIN_IDS = {
    56,  # BNB Chain
    137,  # Polygon
    43114,  # Avalanche C-chain
}


def install_chain_middleware(web3: Web3):
    """Install any chain-specific middleware to Web3 instannce.

    Mainly this is POA middleware for BNB Chain, Polygon, Avalanche C-chain.
    """

    if web3.eth.chain_id in POA_MIDDLEWARE_NEEDED_CHAIN_IDS:
        web3.middleware_onion.inject(geth_poa_middleware, layer=0)


def install_retry_middleware(web3: Web3):
    """Install gracefully HTTP request retry middleware.

    In the case your Internet connection or JSON-RPC node has issues,
    gracefully do exponential backoff retries.
    """
    web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)


def install_api_call_counter_middleware(web3: Web3) -> Counter:
    """Install API call counter middleware.

    Measure total and per-API EVM call counts for your application.

    - Every time a Web3 API is called increase its count.

    - Attach `web3.api_counter` object to the connection

    Example:

    .. code-block:: python

        from eth_defi.chain import install_api_call_counter_middleware

        web3 = Web3(tester)

        counter = install_api_call_counter_middleware(web3)

        # Make an API call
        _ = web3.eth.chain_id

        assert counter["total"] == 1
        assert counter["eth_chainId"] == 1

        # Make another API call
        _ = web3.eth.block_number

        assert counter["total"] == 2
        assert counter["eth_blockNumber"] == 1

    :return:
        Counter object with columns per RPC endpoint and "toal"
    """
    api_counter = Counter()

    def factory(make_request: Callable[[RPCEndpoint, Any], Any], web3: "Web3"):
        def middleware(method: RPCEndpoint, params: Any) -> Optional[RPCResponse]:
            api_counter[method] += 1
            api_counter["total"] += 1
            return make_request(method, params)

        return middleware

    web3.middleware_onion.inject(factory, layer=0)
    return api_counter


def has_graphql_support(provider: HTTPProvider) -> bool:
    """Check if a node has GoEthereum GraphQL API turned on.


    You can check if GraphQL has been turned on for your node with:

    .. code-block:: shell

        curl -X POST \
            https://mynode.example.com/graphql \
            -H "Content-Type: application/json" \
            --data '{ "query": "query { block { number } }" }'

    A valid response looks like::

        {"data":{"block":{"number":16328259}}}
    """

    assert isinstance(provider, HTTPProvider)

    base_url = provider.endpoint_uri
    graphql_url = urljoin(base_url, "graphql")
    try:
        resp = requests.get(graphql_url)
        return resp.status_code == 400
    except Exception as e:
        # ConnectionError, etc.
        return False


def fetch_block_timestamp(web3: Web3, block_number: int) -> datetime.datetime:
    """Get the block mined at timestamp.

    .. warning::

        Uses `eth_getBlock`. Very slow for large number of blocks.
        Use alternative methods for managing timestamps for large block ranges.

    Example:

    .. code-block:: python

        # Get when the first block was mined
        timestamp = fetch_block_timestamp(web3, 1)
        print(timestamp)

    :param web3:
        Web3 connection

    :param block_number:
        Block number of which timestamp we are going to get

    :return:
        UTC naive datetime of the block timestamp
    """
    block = web3.eth.get_block(block_number)
    timestamp = convert_jsonrpc_value_to_int(block["timestamp"])
    time = datetime.datetime.utcfromtimestamp(timestamp)
    return time
