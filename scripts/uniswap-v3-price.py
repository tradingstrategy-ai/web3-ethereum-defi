"""Read a price of a token from Uniswap v3.

- Very simple

- Uses polling approach

- This script is made for Uniswap v3 on Polygon, using free RPC endpoint
"""


import os
import time
import datetime

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.price import get_onchain_price


def main():
    # You can pass your own endpoint in an environment variable
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")

    # Search pair contract addresses using Trading Strategy search: https://tradingstrategy.ai/search
    # This one is:
    # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
    pool_address = os.environ.get("PAIR_ADDRESS", "0x45dda9cb7c25131df268515131f647d726f50608")

    # Create web3 connection instance
    web3 = create_multi_provider_web3(json_rpc_url)

    # Get Pool contract ABI file, prepackaged in eth_defi Python package
    # and convert it to a wrapped Python object
    pool = fetch_pool_details(web3, pool_address)

    # Print out pool details
    # token0 and token1 will be always in a random order
    # and may inverse the price
    print("-" * 80)
    print("Uniswap pool details")
    print("Chain", web3.eth.chain_id)
    print("Pool", pool_address)
    print("Token0", pool.token0.symbol)
    print("Token1", pool.token1.symbol)
    print("Fee", pool.fee)
    print("-" * 80)

    inverse = True  # Is price inverted for output

    # Keep reading events as they land
    while True:

        # Record the block number close to our timestamp
        block_num = web3.eth.get_block_number()

        # Use get_onchain_price() to get a human readable price
        # in Python Decimal
        price = get_onchain_price(
            web3,
            pool.address,
        )

        if inverse:
            price = 1 / price

        timestamp = datetime.datetime.utcnow().isoformat()

        print(f"[{timestamp}, block {block_num:,}] Price {pool.token0.symbol} / {pool.token1.symbol}: {price}")

        # Refresh every 5 seconds
        time.sleep(5)

if __name__ == "__main__":
    main()
