"""Chain specific configuration.

Many chains like Polygon and BNB Chain may need their own Web3 connection tuning.
In this module, we have helpers.
"""

import datetime
from collections import Counter
from typing import Any, Callable, Optional

#: These chains need POA middleware
from urllib.parse import urljoin

import requests
from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware
from web3.providers import BaseProvider, JSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.middleware import http_retry_request_with_sleep_middleware

#: List of chain ids that need to have proof-of-authority middleweare installed
POA_MIDDLEWARE_NEEDED_CHAIN_IDS = {
    56,  # BNB Chain
    137,  # Polygon
    43114,  # Avalanche C-chain
}

#: Manually maintained shorthand names for different EVM chains
CHAIN_NAMES = {
    1: "Ethereum",
    56: "Binance",
    137: "Polygon",
    43114: "Avalanche",
    80094: "Berachain",
    130: "Unichain",
    645749: "Hyperliquid",  # TODO: Not sure what's correct for Hyperliquid
    8453: "Base",
    146: "Sonic",
    34443: "Mode",
    5000: "Mantle",
    999: "Hyperliquid",  # TODO: Not sure what's correct for Hyperliquid
    42161: "Arbitrum",
}

#: Chain avg block times.
#:
#: By Grok, not verified.
EVM_BLOCK_TIMES = {
    1: 12,        # Ethereum (post-Merge, ~12 seconds)
    56: 3,        # Binance Smart Chain (~3 seconds)
    137: 2,       # Polygon PoS (~2 seconds)
    43114: 2,     # Avalanche C-Chain (~2 seconds)
    80094: 1,     # Berachain (assuming ~1 second, based on high-performance claims; may need verification)
    130: 1,       # Unichain (estimated ~1 second, as a high-throughput chain; confirm with official docs)
    645749: 0.1,  # Hyperliquid (speculative: ~100ms, based on its high-speed design; adjust as needed)
    8453: 2,      # Base (~2 seconds, aligned with Optimism rollup timing)
    146: 1,       # Sonic (estimated ~1 second, designed for speed; confirm with official sources)
    34443: 2,     # Mode (~2 seconds, typical for Optimistic rollups)
    5000: 2,      # Mantle (~2 seconds, based on its Ethereum L2 design)
    999: 0.1,     # Hyperliquid (same as 645749, assuming chain ID confusion; verify correct ID)
    42161: 0.25   # Arbitrum (block time ~250ms, though batches vary; reflects Nitro update)
}


def get_chain_name(chain_id: int) -> str:
    """Translate Ethereum chain id to its name."""
    name = CHAIN_NAMES.get(chain_id)
    if name:
        return name

    return f"<Unknown chain, id {chain_id}>"


def install_chain_middleware(web3: Web3, poa_middleware=None):
    """Install any chain-specific middleware to Web3 instance.

    Mainly this is POA middleware for BNB Chain, Polygon, Avalanche C-chain.

    Example:

    .. code-block:: python

        web3 = Web3(HTTPProvider(json_rpc_url))
        print(f"Connected to blockchain, chain id is {web3.eth.chain_id}. the latest block is {web3.eth.block_number:,}")

        # Read and setup a local private key
        private_key = os.environ.get("PRIVATE_KEY")
        assert private_key is not None, "You must set PRIVATE_KEY environment variable"
        assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"
        account: LocalAccount = Account.from_key(private_key)
        web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

        # Support Polygon, BNG chain
        install_chain_middleware(web3)

        # ... code goes here...z
        tx_hash = erc_20.functions.transfer(to_address, raw_amount).transact({"from": account.address})

    :param poa_middleware:
        If set, force the installation of proof-of-authority GoEthereum middleware.

        Needed e.g. when using forked Polygon with Anvil.

    """

    if poa_middleware is None:
        poa_middleware = web3.eth.chain_id in POA_MIDDLEWARE_NEEDED_CHAIN_IDS

    if poa_middleware:
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
        Counter object with columns per RPC endpoint and "total"
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


def install_api_call_counter_middleware_on_provider(provider: JSONBaseProvider) -> Counter:
    """Install API call counter middleware on a specific API provider.

    Allows per-provider API call counting when using complex
    provider setups.

    See also

    - :py:func:`install_api_call_counter_middleware`

    - :py:class:`eth_defi.fallback_provider.FallbackProvider`

    :return:
        Counter object with columns per RPC endpoint and "total"
    """

    assert isinstance(provider, JSONBaseProvider), f"Got {provider.__class__}"

    api_counter = Counter()

    def factory(make_request: Callable[[RPCEndpoint, Any], Any], web3: "Web3"):
        def middleware(method: RPCEndpoint, params: Any) -> Optional[RPCResponse]:
            api_counter[method] += 1
            api_counter["total"] += 1
            return make_request(method, params)

        return middleware

    provider.middlewares.add("api_counter_middleware", factory)
    return api_counter


def get_graphql_url(provider: BaseProvider) -> str:
    """Resolve potential GraphQL endpoint API for a JSON-RPC provider.

    See :py:func:`has_graphql_support`.
    """

    # See BaseNamedProvider
    if hasattr(provider, "call_endpoint_uri"):
        base_url = provider.call_endpoint_uri
    elif hasattr(provider, "endpoint_uri"):
        # HTTPProvider
        base_url = provider.endpoint_uri
    else:
        raise AssertionError(f"Do not know how to extract endpoint URI: {provider}")

    # make sure base url contains a trailing slash so urljoin() below works correctly
    if not base_url.endswith("/"):
        base_url += "/"

    graphql_url = urljoin(base_url, "graphql")

    return graphql_url


def has_graphql_support(provider: BaseProvider) -> bool:
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

    graphql_url = get_graphql_url(provider)

    try:
        resp = requests.get(graphql_url, json={"query": "query{block{number}}"})
        return resp.status_code == 200 and resp.json()["data"]["block"]["number"]
    except Exception as e:
        # ConnectionError, RequestsJSONDecodeError, etc.
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


def install_retry_middleware(web3: Web3):
    """Install gracefully HTTP request retry middleware.

    In the case your Internet connection or JSON-RPC node has issues,
    gracefully do exponential backoff retries.
    """
    web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)
