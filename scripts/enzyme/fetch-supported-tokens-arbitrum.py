""" "Print out enzyme supported tokens.

Needs an Arbitrum node.

Example:

.. code-block:: shell

    export JSON_RPC_ARBITRUM=...
    python scripts/enzyme/fetch-supported-tokens-arbitrum.py

"""

import datetime
import logging
import os
from typing import List

from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.chainlink.round_data import fetch_chainlink_round_data
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment, ARBITRUM_DEPLOYMENT
from eth_defi.enzyme.price_feed import fetch_price_feeds, EnzymePriceFeed, UnsupportedBaseAsset
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate
from eth_defi.token import fetch_erc20_details


def main():
    # Set up stdout logger
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    assert json_rpc_url, f"You need to give JSON_RPC_ARBITRUM environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    end_block = os.environ.get("END_BLOCK")
    if end_block:
        end_block = int(end_block)
    else:
        end_block = web3.eth.block_number

    # Read Enzyme deployment from chain
    deployment = EnzymeDeployment.fetch_deployment(web3, ARBITRUM_DEPLOYMENT)
    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Check what base price feeds we have
    value_interpreter = deployment.contracts.value_interpreter
    weth_token = fetch_erc20_details(web3, value_interpreter.functions.getWethToken().call())
    # EACAggregatorProxy
    # https://polygonscan.com/address/0xF9680D99D6C9589e2a93a78A04A279e509205945#code
    eth_usd_aggregator = value_interpreter.functions.getEthUsdAggregator().call()
    round_data = fetch_chainlink_round_data(web3, eth_usd_aggregator)
    print(f"Enzyme's WETH token is set to: {weth_token}")
    print(f"ETH-USD aggregator set to: {eth_usd_aggregator}")
    print(f"ETH-USD price updated: {native_datetime_utc_now() - round_data.update_time} ago")
    print(f"ETH-USD latest price: {round_data.price} by the feed {round_data.description}")

    # Check ETH chainlink price always at the start
    # weth = fetch_erc20_details(web3, POLYGON_DEPLOYMENT["weth"])
    # feed = EnzymePriceFeed.fetch_price_feed(deployment, weth)
    # print(f"ETH Chainlink aggregator is at {feed.aggregator}")
    # round_data = feed.fetch_latest_round_data()
    # update_ago = native_datetime_utc_now() - round_data.update_time
    # print(f"ETH price is {round_data}, updated {update_ago} ago")
    # import ipdb ; ipdb.set_trace()

    # Set up multithreaded Polygon event reader.
    # Print progress to the console how many blocks there are left to read.
    reader = MultithreadEventReader(json_rpc_url, max_threads=16, notify=PrintProgressUpdate(), max_blocks_once=10_000)

    # Iterate through all events
    feeds: List[EnzymePriceFeed] = []
    for price_feed in fetch_price_feeds(
        deployment,
        start_block=POLYGON_DEPLOYMENT["deployed_at"],
        end_block=end_block,
        read_events=reader,
    ):
        feeds.append(price_feed)

    reader.close()

    print("Found Enzyme price feeds")
    usdc = fetch_erc20_details(web3, ARBITRUM_DEPLOYMENT["usdc"])
    for feed in feeds:
        try:
            price = feed.calculate_current_onchain_price(usdc)
            aggregator = feed.chainlink_aggregator
            round_data = fetch_chainlink_round_data(web3, aggregator.address)
            ago = native_datetime_utc_now() - round_data.update_time
            print(f"   {feed.primitive_token.symbol}, current price is {price:,.4f} USDC, Chainlink feed is {round_data.description}, updated {ago} ago")
        except UnsupportedBaseAsset as e:
            print(f"   {feed.primitive_token.symbol} price feed not available: unsupported base asset")

    print("[")
    for pf in feeds:
        token = fetch_erc20_details(web3, pf.primitive)
        print(f'("{token.address}", "{token.name}", "{token.symbol}"),')
    print("]")

    print(f"Total {len(feeds)} Enzyme price feeds")


if __name__ == "__main__":
    main()
