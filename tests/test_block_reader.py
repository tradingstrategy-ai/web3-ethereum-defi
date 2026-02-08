"""Fast event reading.

For manual tests see `scripts/read-uniswap-v2-pairs-and-swaps.py`.

.. code-block:: shell

    # Ethereum JSON-RPC
    export JSON_RPC_URL=
    pytest -k test_revert_reason

"""

import os

import flaky
import pytest
import requests
from requests.adapters import HTTPAdapter
from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import (
    convert_uint256_bytes_to_address,
    convert_uint256_string_to_address,
    decode_data,
    convert_jsonrpc_value_to_int,
)
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.logresult import LogContext, LogResult
from eth_defi.event_reader.reader import read_events, read_events_concurrent
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDetails, fetch_erc20_details

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="Set JSON_RPC_ETHEREUM environment variable to Ethereum mainnet node to run this test",
)


class TokenCache(LogContext):
    """Manage cache of token data when doing PairCreated look-up.

    Do not do extra requests for already known tokens.
    """

    def __init__(self):
        self.cache = {}

    def get_token_info(self, web3: Web3, address: str) -> TokenDetails:
        if address not in self.cache:
            self.cache[address] = fetch_erc20_details(web3, address, raise_on_error=False)
        return self.cache[address]


def decode_pair_created(web3: Web3, log: LogResult) -> dict:
    """Process a pair created event.

    This function does manually optimised high speed decoding of the event.

    The event signature is:

    .. code-block::

        event PairCreated(address indexed token0, address indexed token1, address pair, uint);
    """

    # The raw log result looks like
    # {'address': '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f', 'blockHash': '0x359d1dc4f14f9a07cba3ae8416958978ce98f78ad7b8d505925dad9722081f04', 'blockNumber': '0x98b723', 'data': '0x000000000000000000000000b4e16d0168e52d35cacd2c6185b44281ec28c9dc0000000000000000000000000000000000000000000000000000000000000001', 'logIndex': '0x22', 'removed': False, 'topics': ['0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9', '0x000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0x000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'], 'transactionHash': '0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a', 'transactionIndex': '0x26', 'event': <class 'web3._utils.datatypes.PairCreated'>, 'timestamp': 1588710145}

    # Do additional lookup for the token data
    token_cache: TokenCache = log["context"]

    # Any indexed Solidity event parameter will be in topics data.
    # The first topics (0) is always the event signature.
    token0_address = convert_uint256_string_to_address(log["topics"][1])
    token1_address = convert_uint256_string_to_address(log["topics"][2])

    factory_address = log["address"]

    # Chop data blob to byte32 entries
    data_entries = decode_data(log["data"])

    # Any non-indexed Solidity event parameter will be in the data section.
    pair_contract_address = convert_uint256_bytes_to_address(data_entries[0])
    pair_count = int.from_bytes(data_entries[1], "big")

    # Now enhanche data with token information
    token0 = token_cache.get_token_info(web3, token0_address)
    token1 = token_cache.get_token_info(web3, token1_address)

    data = {
        "block_number": convert_jsonrpc_value_to_int(log["blockNumber"]),
        "tx_hash": log["transactionHash"],
        "log_index": convert_jsonrpc_value_to_int(log["logIndex"]),
        "factory_contract_address": factory_address,
        "pair_contract_address": pair_contract_address,
        "pair_count_index": pair_count,
        "token0_symbol": token0.symbol,
        "token0_address": token0_address,
        "token1_symbol": token1.symbol,
        "token1_address": token1_address,
    }
    return data


@flaky.flaky(max_runs=3)
def test_read_events():
    """Read events quickly over JSON-RPC API."""

    json_rpc_url = JSON_RPC_ETHEREUM
    web3 = create_multi_provider_web3(json_rpc_url, hint="Ethereum, token cache test")

    # Get contracts
    Factory = get_contract(web3, "sushi/UniswapV2Factory.json")

    events = [
        Factory.events.PairCreated,  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
    ]

    token_cache = TokenCache()

    start_block = 10_000_835  # Uni deployed
    end_block = 10_009_000  # The first pair created before this block

    # Read through the blog ran
    out = []
    for log_result in read_events(
        web3,
        start_block,
        end_block,
        events,
        None,
        chunk_size=1000,
        context=token_cache,
        extract_timestamps=None,
    ):
        out.append(decode_pair_created(web3, log_result))

    assert len(out) == 2

    e = out[0]
    assert e["pair_count_index"] == 1
    assert e["pair_contract_address"] == "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    assert e["token1_symbol"] == "WETH"
    assert e["token0_symbol"] == "USDC"
    assert e["tx_hash"] == "0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a"

    e = out[1]
    assert e["pair_count_index"] == 2
    assert e["token1_symbol"] == "USDC"
    assert e["token0_symbol"] == "USDP"
    assert e["tx_hash"] == "0xb0621ca74cee9f540dda6d575f6a7b876133b42684c1259aaeb59c831410ccb2"


@flaky.flaky(max_runs=3)
def test_read_events_concurrent():
    """Read events quickly over JSON-RPC API using a thread pool."""

    token_cache = TokenCache()
    threads = 16

    json_rpc_url = JSON_RPC_ETHEREUM
    web3_factory = MultiProviderWeb3Factory(json_rpc_url, hint="Ethereum, token cache concurrent test")
    web3 = web3_factory()
    executor = create_thread_pool_executor(web3_factory, token_cache, max_workers=threads)

    # Get contracts
    Factory = get_contract(web3, "sushi/UniswapV2Factory.json")

    events = [
        Factory.events.PairCreated,  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
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
        out.append(decode_pair_created(web3, log_result))

    assert len(out) == 2

    e = out[0]
    assert e["pair_count_index"] == 1
    assert e["pair_contract_address"] == "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    assert e["token1_symbol"] == "WETH"
    assert e["token0_symbol"] == "USDC"
    assert e["tx_hash"] == "0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a"

    e = out[1]
    assert e["pair_count_index"] == 2
    assert e["token1_symbol"] == "USDC"
    assert e["token0_symbol"] == "USDP"
    assert e["tx_hash"] == "0xb0621ca74cee9f540dda6d575f6a7b876133b42684c1259aaeb59c831410ccb2"


@flaky.flaky(max_runs=3)
def test_read_events_concurrent_two_nodes():
    """TunedWeb3Factory accepts fallover nodes as config."""

    json_rpc_url = JSON_RPC_ETHEREUM
    token_cache = TokenCache()
    threads = 16
    config_line = json_rpc_url + " " + json_rpc_url + "?foo=bar"  # Fake
    json_rpc_url = JSON_RPC_ETHEREUM
    web3 = create_multi_provider_web3(json_rpc_url, hint="Ethereum, token cache concurrent test")
    web3_factory = MultiProviderWeb3Factory(json_rpc_url, hint="Ethereum, token cache concurrent test")
    executor = create_thread_pool_executor(web3_factory, token_cache, max_workers=threads)

    # Get contracts
    Factory = get_contract(web3, "sushi/UniswapV2Factory.json")

    events = [
        Factory.events.PairCreated,  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
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
        out.append(decode_pair_created(web3, log_result))

    assert len(out) == 2
