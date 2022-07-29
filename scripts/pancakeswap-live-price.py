"""Show live BNB/BUSD price from PancakeSwap pool.

- Show the latest price

- Show the TWAP price

Also

- Uses HTTP polling method

- Adjusts for minor chain reorgs / unstable chain tip

To run:

.. code-block:: python

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    python scripts/pancakeswap-live-price.py

"""
import datetime
import os
import time

from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware

from eth_defi.price_oracle.oracle import PriceOracle, time_weighted_average_price
from eth_defi.uniswap_v2.oracle import update_live_price_feed
from eth_defi.uniswap_v2.pair import fetch_pair_details


def main():
    json_rpc_url = os.environ["BNB_CHAIN_JSON_RPC"]

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    # https://tradingstrategy.ai/trading-view/binance/pancakeswap-v2/bnb-busd
    pair_contract_address = "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"

    reverse_token_order = False

    pair_details = fetch_pair_details(web3, pair_contract_address)

    print(f"Displaying live and TWAP price for {pair_details.token0.symbol} - {pair_details.token1.symbol}")

    price_ticker = f"{pair_details.token0.symbol}/{pair_details.token1.symbol}"

    oracle = PriceOracle(
        time_weighted_average_price,
        max_age=datetime.timedelta(minutes=15),  # Crash if we data gets more stale than 15 minutes
        min_duration=datetime.timedelta(minutes=1),
    )

    # How fast BNB Smart chain ticks
    block_time = 3.0

    initial_fetch_safety_margin = 1.2

    # To back fill the oracle buffer,
    # unitially fetch data for the latest time window blocks plus 20% safety margin
    initial_fetch_block_count = int(oracle.target_time_window / datetime.timedelta(seconds=block_time) * initial_fetch_safety_margin)

    print(f"Starting initial data fetch of {initial_fetch_block_count} blocks")
    update_live_price_feed(oracle, web3, pair_contract_address, reverse_token_order=reverse_token_order, lookback_block_count=initial_fetch_block_count)

    print(f"Starting live price feed, TWAP time window is set to {oracle.target_time_window}")
    while True:
        stats = update_live_price_feed(oracle, web3, pair_contract_address, reverse_token_order=reverse_token_order)

        last_price = oracle.get_newest().price
        twap = oracle.calculate_price()

        oldest = oracle.get_oldest()
        newest = oracle.get_newest()

        print(f"Block {oracle.last_refreshed_block_number:,} at {oracle.last_refreshed_at} current price:{last_price:.4f} {price_ticker} TWAP:{twap:.4f} {price_ticker}")
        print(f"    Oracle data updates: {stats}, trades in TWAP buffer:{len(oracle.buffer)}, oldest:{oldest.timestamp}, newest:{newest.timestamp} ")
        time.sleep(block_time)


if __name__ == "__main__":
    main()
