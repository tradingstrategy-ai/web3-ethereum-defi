"""Multicall3 example on Base.

- Uses :py:mod:`eth_defi.event_reader.multicall_batcher` module to do various reads using `Multicall3 contract <https://www.multicall3.com/>`__

To run:

.. code-block:: shell

    export JSON_RPC_BASE=<get your own RPC URL>
    python scripts/base/multicall3-example.py
"""

import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

import eth_abi
from IPython.core.completer import TypedDict
from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3, UniswapV3Deployment


from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.utils import encode_path
from eth_defi.utils import setup_console_logging


logging.basicConfig(level=logging.INFO, stream=sys.stdout)

#: How many pairs try to ask in this example
#:
#: Multicall Python code has internal chunking
NUMBER_OF_PAIRS = 200

#: "contract_instance" or "abi_encode"
METHOD = os.environ.get("EXAMPLE_METHOD", "contract_instance")


class PoolData(TypedDict):
    base: str
    quote: str
    fee: int  # BPS
    pool_address: HexAddress
    quote_token_address: HexAddress
    base_token_address: HexAddress
    base_token_decimals: int
    quote_token_decimals: int


def main():
    setup_console_logging(default_log_level="info")

    # See https://web3-ethereum-defi.readthedocs.io/tutorials/mev-blocker.html how to configure RPC
    rpc_configuration_line = os.environ.get("JSON_RPC_BASE")
    assert rpc_configuration_line, "This script is too heavy to perform on free RPC. Get your own RPC provider and set it it as JSON_RPC_BASE environment variable"

    web3 = create_multi_provider_web3(rpc_configuration_line)

    # We are reading using subprocesses, so we need to pass a factory function over Python process boundaries
    web3factory = MultiProviderWeb3Factory(rpc_configuration_line)

    assert web3.eth.chain_id == 8453  # Example is for Base only

    # Read Uniswap v3 deployment data on Base
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3: UniswapV3Deployment = fetch_deployment_uni_v3(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
        router_v2=deployment_data["router_v2"],
    )

    # Read example pool data, dumped as JSON file in the repo.
    # See tutorial https://github.com/tradingstrategy-ai/getting-started/blob/master/scratchpad/uniswap-v3-pool-data/01-uniswap-v3-pools-on-base.ipynb
    # on how to generate this data.
    pool_data: list[PoolData]
    pool_data = json.load((Path(__file__).parent / "pools.json").open("rt"))

    # Choose random N pools
    pool_data = [p for p in pool_data if p["quote"] == "USDC"]
    pools_to_read = pool_data[0:NUMBER_OF_PAIRS]
    amount_in = 1_000_000  # 1 USDC in raw units

    # Example 1:
    # Do a Multicall3 read for Uniswap v3 prices using Web3.py proxy contracts to construct ABI payload
    def _create_call_using_proxy_class(pool_data: PoolData) -> EncodedCall:
        # Uniswap v3 internal path struct
        path_bytes = encode_path(
            path=[pool_data["quote_token_address"], pool_data["base_token_address"]],
            fees=[pool_data["fee"] * 100],
        )
        bound_func: ContractFunction
        bound_func = uniswap_v3.quoter.functions.quoteExactInput(
            path_bytes,
            amount_in,
        )
        # Add a pool hint as an extra data for which pool this call is
        return EncodedCall.from_contract_call(bound_func, extra_data={"pool_address": pool_data["pool_address"]})

    # Example 2:
    # Do a Multicall3 read for Uniswap v3 prices using raw abi_encode
    def _create_call_using_raw_abi(pool_data: PoolData) -> EncodedCall:
        path_bytes = encode_path(
            path=[pool_data["quote_token_address"], pool_data["base_token_address"]],
            fees=[pool_data["fee"] * 100],
        )
        signature_string = "quoteExactInput(bytes,uint256)(uint256,uint160[],uint32[],uint256)"
        signature_4bytes = Web3.keccak(text=signature_string)[0:4]
        packed_args = eth_abi.encode(["bytes", "uint256"], [path_bytes, amount_in])
        # Add a pool hint as an extra data for which pool this call is
        return EncodedCall.from_keccak_signature(
            address=uniswap_v3.quoter.address,
            function="quoteExactInput",  # For debug
            signature=signature_4bytes,
            data=packed_args,
            extra_data={"pool_address": pool_data["pool_address"]},
        )

    match METHOD:
        case "contract_instance":
            # Use contract instance to create a call
            encoded_calls = [_create_call_using_proxy_class(p) for p in pools_to_read]
        case "abi_encode":
            # Use raw ABI strings to create a call
            encoded_calls = [_create_call_using_raw_abi(p) for p in pools_to_read]
        case _:
            raise NotImplementedError(f"Unknown example method {METHOD}")

    # Ask a block a bit behind unstable tip to avoid RPC crashes
    block_number = get_almost_latest_block_number(web3)

    # Create MultiProcess machinery to do X calls per chunk,
    # stream responses with a Python iterator
    results = read_multicall_chunked(
        chain_id=web3.eth.chain_id,
        web3factory=web3factory,
        calls=encoded_calls,
        block_identifier=block_number,
        max_workers=1,  # Set max_workers=1 for debugging, max_workers=8 for speed
        chunk_size=40,
        progress_bar_desc=None,  # No progress bar
    )

    # Print results
    pool_map = {p["pool_address"]: p for p in pools_to_read}
    result: EncodedCallResult
    for idx, result in enumerate(results):
        pool_address = result.call.extra_data["pool_address"]
        pool = pool_map[pool_address]
        pool_name = f"{pool['base']}/{pool['quote']}@{pool['fee']} BPS"
        block_number = result.block_identifier

        if result.success:
            # WE unpack the QuoterV2 reply struct by hand
            #         returns (
            #             uint256 amountOut,
            #             uint160[] memory sqrtPriceX96AfterList,
            #             uint32[] memory initializedTicksCrossedList,
            #             uint256 gasEstimate
            #         );

            price_raw = convert_int256_bytes_to_int(result.result[0:32])
            price_decimals = Decimal(price_raw) / Decimal(10 ** pool["base_token_decimals"])
            price = Decimal(1) / price_decimals
            print(f"Pool {idx + 1}: {pool_name}: price {price} {pool['base']} / {pool['quote']}, at block {block_number}")
        else:
            print(f"Pool {idx + 1}: {pool_name}: call failed, debug details:\n{result.call.get_debug_info()}")


if __name__ == "__main__":
    main()
