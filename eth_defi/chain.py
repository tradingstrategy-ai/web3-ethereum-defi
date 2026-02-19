"""Chain specific configuration.

Many chains like Polygon and BNB Chain may need their own Web3 connection tuning.
In this module, we have helpers.
"""

import datetime
import logging
from collections import Counter
from typing import Any, Callable, Optional

#: These chains need POA middleware
from urllib.parse import urljoin

import requests
from web3 import HTTPProvider, Web3
from web3.providers import BaseProvider, JSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.middleware import http_retry_request_with_sleep_middleware
from eth_defi.provider.named import get_provider_name
from eth_defi.compat import native_datetime_utc_fromtimestamp

#: List of chain ids that need to have proof-of-authority middleweare installed
POA_MIDDLEWARE_NEEDED_CHAIN_IDS = {
    56,  # BNB Chain
    137,  # Polygon
    43114,  # Avalanche C-chain
}

#: Manually maintained shorthand names for different EVM chains
CHAIN_NAMES = {
    -1: "Hypercore",  # Synthetic in-house ID for native Hyperliquid vaults (non-EVM)
    1: "Ethereum",
    56: "Binance",
    137: "Polygon",
    43114: "Avalanche",
    80094: "Berachain",
    130: "Unichain",
    645749: "Hyperliquid",  # Legacy chain ID, see also 999 (HyperEVM)
    8453: "Base",
    146: "Sonic",
    34443: "Mode",
    5000: "Mantle",
    999: "Hyperliquid",  # HyperEVM, see https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm
    42161: "Arbitrum",
    11155111: "Ethereum_Sepolia",
    421614: "Arbitrum_Sepolia",
    84532: "Base_Sepolia",
    #
    2741: "Abstract",
    10: "Optimism",
    1868: "Soneium",
    324: "ZKsync",
    100: "Gnosis",
    81457: "Blast",
    42220: "Celo",
    7777777: "Zora",
    57073: "Ink",
    #
    9745: "Plasma",
    239: "TAC",
    43111: "Hemi",
    59144: "Linea",
    747474: "Katana",
    143: "Monad",
    957: "Derive",
}

#: For linking on reports
CHAIN_HOMEPAGES = {
    1: {"name": "Ethereum", "homepage": "https://ethereum.org"},
    56: {"name": "Binance", "homepage": "https://www.bnbchain.org"},
    137: {"name": "Polygon", "homepage": "https://polygon.technology"},
    43114: {"name": "Avalanche", "homepage": "https://www.avax.network"},
    80094: {"name": "Berachain", "homepage": "https://www.berachain.com"},
    130: {"name": "Unichain", "homepage": "https://www.uniswap.org/unichain"},  # Uniswap's Unichain
    645749: {"name": "Hyperliquid", "homepage": "https://hyperliquid.xyz"},  # Legacy chain ID, see also 999
    8453: {"name": "Base", "homepage": "https://www.base.org"},
    146: {"name": "Sonic", "homepage": "https://www.soniclabs.com/"},  # Formerly Fantom Sonic
    34443: {"name": "Mode", "homepage": "https://www.mode.network"},
    5000: {"name": "Mantle", "homepage": "https://www.mantle.xyz"},
    999: {"name": "Hyperliquid", "homepage": "https://hyperliquid.xyz"},  # HyperEVM, see https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm
    42161: {"name": "Arbitrum", "homepage": "https://arbitrum.io"},
    11155111: {"name": "Ethereum Sepolia", "homepage": "https://ethereum.org"},
    421614: {"name": "Arbitrum Sepolia", "homepage": "https://arbitrum.io"},
    84532: {"name": "Base Sepolia", "homepage": "https://www.base.org"},
    2741: {"name": "Abstract", "homepage": "https://www.abstract.foundation"},  # Limited info, assumed official
    10: {"name": "Optimism", "homepage": "https://www.optimism.io"},
    1868: {"name": "Soneium", "homepage": "https://www.soneium.org"},
    324: {"name": "ZKsync", "homepage": "https://zksync.io"},
    100: {"name": "Gnosis", "homepage": "https://www.gnosis.io"},
    81457: {"name": "Blast", "homepage": "https://blast.io"},
    42220: {"name": "Celo", "homepage": "https://celo.org"},
    7777777: {"name": "Zora", "homepage": "https://zora.co"},
    57073: {"name": "Ink", "homepage": "https://inkonchain.com/"},
    9745: {"name": "Plasma", "homepage": "https://www.plasma.to/"},
    239: {"name": "TAC", "homepage": "https://tac.build/"},
    43111: {"name": "Hemi", "homepage": "https://hemi.xyz/"},
    59144: {"name": "Linea", "homepage": "https://linea.build/"},
    747474: {"name": "Katana", "homepage": "https://katana.network/"},
    143: {"name": "Monad", "homepage": "https://monad.xyz"},
    957: {"name": "Derive", "homepage": "https://derive.xyz"},
}

#: Chain shortest block times in seconds.
#:
#: Used to convert between block ranges and wall-clock durations.
#: For chains with multiple block types (e.g. HyperEVM dual-block architecture),
#: we use the **shortest** block time so that block-range → time estimates are accurate.
#:
#: Note that for many chains these are approximate and can vary based on network conditions and upgrades.
#:
EVM_BLOCK_TIMES = {
    1: 12,  # Ethereum (post-Merge, ~12 seconds)
    56: 3,  # Binance Smart Chain (~3 seconds)
    137: 2,  # Polygon PoS (~2 seconds)
    43114: 2,  # Avalanche C-Chain (~2 seconds)
    80094: 1,  # Berachain (assuming ~1 second, based on high-performance claims; may need verification)
    130: 1,  # Unichain (estimated ~1 second, as a high-throughput chain; confirm with official docs)
    645749: 1,  # HyperEVM dual-block: small blocks (2M gas, ~1s), large blocks (30M gas, ~60s). See chain ID 999 comment.
    8453: 2,  # Base (~2 seconds, aligned with Optimism rollup timing)
    146: 1,  # Sonic (estimated ~1 second, designed for speed; confirm with official sources)
    34443: 2,  # Mode (~2 seconds, typical for Optimistic rollups)
    5000: 2,  # Mantle (~2 seconds, based on its Ethereum L2 design)
    #: HyperEVM uses a dual-block architecture: small blocks (2M gas, ~1s) and large blocks (30M gas, ~60s).
    #: Contract deployments >2M gas require opting in to large blocks via ``evmUserModify`` with ``usingBigBlocks``.
    #: See `HyperEVM dual-block architecture <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture>`__.
    999: 1,  # HyperEVM dual-block: small blocks (2M gas, ~1s), large blocks (30M gas, ~60s). Using shortest.
    42161: 0.25,  # Arbitrum (block time ~250ms, though batches vary; reflects Nitro update)
    11155111: 12,  # Ethereum Sepolia (same as Ethereum mainnet)
    421614: 0.25,  # Arbitrum Sepolia (same as Arbitrum mainnet)
    84532: 2,  # Base Sepolia (same as Base mainnet)
    2741: 2,  # Layer-2, assumed fast EVM_BLOCK_TIMESlike other L2s
    10: 2,  # Optimistic Rollup, ~2s block time
    1868: 2,  # New L2, assumed ~2s based on typical L2 performance
    324: 1,  # ZK-Rollup, very fast, ~1s
    100: 5,  # Gnosis Chain, ~5s
    81457: 2,  # Layer-2, ~2s typical for Optimistic-style chains
    42220: 5,  # Celo, ~5s block time
    7777777: 2,  # Zora L2
    57073: 2,  # Ink (Optimism)
    # New batch
    9745: 1,  # Plasma is "subsecond" but there is no stable blockchain, so this is just workaround https://www.plasma.to/chain
    239: 1.5,  # TAC, ~1.5s averaged
    43111: 12,  # Hemi aims for sub-15 second block times on its Layer 2 network
    59144: 2,  # Block Time: Approximately 2 seconds soft finality
    747474: 1,  # Katana, ~1s block time avg
    143: 0.4,  # Monad, target ~1s block times on mainnet
    957: 2,  # Derive (Lyra L2), ~2s block time
}


def get_chain_homepage(chain_id: int) -> tuple[str, str]:
    """Translate Ethereum chain id to a link to its homepage.

    :return:
        name, homepage link tuple
    """
    name = CHAIN_NAMES.get(chain_id)
    link = CHAIN_HOMEPAGES.get(chain_id)
    if not name or not link:
        return f"<Unknown chain , id {chain_id}>", "https://"

    return name, link["homepage"]


def get_chain_name(chain_id: int) -> str:
    """Translate Ethereum chain id to its name."""
    name = CHAIN_NAMES.get(chain_id)
    if name:
        return name

    return f"<Unknown chain, id {chain_id}>"


def get_chain_id_by_name(name: str) -> Optional[int]:
    """Get chain id by its name.

    :param name:
        Case-insensitive chain name, e.g. "Ethereum", "Polygon", "BNB Chain"

    :return:
        Chain id or None if not found
    """
    name_lower = name.lower()
    for chain_id, chain_name in CHAIN_NAMES.items():
        if chain_name.lower() == name_lower:
            return chain_id
    return None


def get_block_time(chain_id: int) -> float:
    """Get average block time for a chain.

    :param chain_id:
        Chain id to get the block time for

    :return:
        Average block time in seconds.
    """
    block_time = EVM_BLOCK_TIMES.get(chain_id)
    assert block_time is not None, f"Unknown chain id {chain_id} {get_chain_name()} for block time lookup table"
    return block_time


def get_default_call_gas_limit(chain_id: int) -> int:
    """Get the eth_call reasonable gas limit.

    - 15M except for Mantle 99M
    - Mantle has weird policy and all transactions and calls cost much more than other chains
    """
    assert type(chain_id) == int, f"Got: {chain_id}"
    if chain_id == 5000:
        return 99_000_000
    else:
        return 15_000_000


def install_chain_middleware(web3: Web3, poa_middleware=None, hint: str = ""):
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

    :param hint:
        Optional hint for error logs when something goes wrong. Useful for debugging and logging.

    """

    if poa_middleware is None:
        try:
            poa_middleware = web3.eth.chain_id in POA_MIDDLEWARE_NEEDED_CHAIN_IDS
        except Exception as e:
            # Github WTF
            name = get_provider_name(web3.provider)
            raise RuntimeError(f"Could not call eth_chainId on {name} provider. Is it a valid JSON-RPC provider? As this is often the first call, you might be also out of API credits. Hint is {hint}") from e

    if poa_middleware:
        from web3.middleware import ExtraDataToPOAMiddleware

        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)


def install_retry_middleware(web3: Web3):
    """Install gracefully HTTP request retry middleware.

    In the case your Internet connection or JSON-RPC node has issues,
    gracefully do exponential backoff retries.
    """
    from requests.exceptions import ConnectionError, HTTPError, Timeout
    from web3.providers.rpc.utils import ExceptionRetryConfiguration

    provider = web3.provider
    if hasattr(provider, "exception_retry_configuration"):
        provider.exception_retry_configuration = ExceptionRetryConfiguration(
            errors=(ConnectionError, HTTPError, Timeout),
            retries=10,
            backoff_factor=0.5,
        )


def install_api_call_counter_middleware(web3: Web3) -> Counter:
    """Install API call counter middleware.

    Measure total and per-API EVM call counts for your application.

    - Every time a Web3 API is called increase its count.
    - Attach `web3.api_counter` object to the connection

    Compatible with both web3.py v6 and v7.

    Example:

    .. code-block:: python

        from eth_defi.chain import install_api_call_counter_middleware

        web3 = Web3(tester)
        counter = install_api_call_counter_middleware(web3)

        # Make an API call
        chain_id = web3.eth.chain_id
        assert counter["total"] == 1
        assert counter["eth_chainId"] == 1

        # Make another API call
        block_number = web3.eth.block_number
        assert counter["total"] == 2
        assert counter["eth_blockNumber"] == 1

    :return:
        Counter object with columns per RPC endpoint and "total"
    """

    from web3.middleware import Web3Middleware

    api_counter = Counter()

    def create_counter_middleware():
        class APICallCounterMiddleware(Web3Middleware):
            def wrap_make_request(self, make_request):
                def middleware(method, params):
                    api_counter[method] += 1
                    api_counter["total"] += 1
                    return make_request(method, params)

                return middleware

        return APICallCounterMiddleware

    middleware_class = create_counter_middleware()
    web3.middleware_onion.inject(middleware_class, layer=0)

    return api_counter


def install_api_call_counter_middleware_on_provider(provider: JSONBaseProvider) -> Counter:
    """Install API call counter middleware on a specific API provider.

    Allows per-provider API call counting when using complex
    provider setups.

    Compatible with both web3.py v6 and v7.

    See also

    - :py:func:`install_api_call_counter_middleware`
    - :py:class:`eth_defi.fallback_provider.FallbackProvider`

    :return:
        Counter object with columns per RPC endpoint and "total"
    """

    logger = logging.getLogger(__name__)

    assert isinstance(provider, JSONBaseProvider), f"Got {provider.__class__}"
    api_counter = Counter()

    logger.warning("install_api_call_counter_middleware_on_provider() is deprecated. Provider-level middleware is discouraged in web3.py v7. Consider using install_api_call_counter_middleware() on the Web3 instance instead.")

    if hasattr(provider, "middlewares") and hasattr(provider.middlewares, "add"):

        def factory(make_request: Callable[[RPCEndpoint, Any], Any], web3: Web3):
            def middleware(method: RPCEndpoint, params: Any) -> Optional[RPCResponse]:
                api_counter[method] += 1
                api_counter["total"] += 1
                return make_request(method, params)

            return middleware

        try:
            provider.middlewares.add("api_counter_middleware", factory)
        except (AttributeError, TypeError) as e:
            logger.error("Cannot install provider-level middleware: %s. Provider type: %s. Use install_api_call_counter_middleware() on Web3 instance instead.", e, type(provider))
    else:
        logger.error("Provider %s does not support middleware installation. Use install_api_call_counter_middleware() on Web3 instance instead.", type(provider))

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
    time = native_datetime_utc_fromtimestamp(timestamp)
    return time


def install_retry_muiddleware(web3: Web3):
    return install_retry_middleware_compat(web3)
