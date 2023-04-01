""""Read Enzyme price feeds configured Polygon.

Manual test script to print out information for a single Enzyme vault.

Needs Polygon full node. Get one from QuickNode.

Example:

.. code-block:: shell

    export JSON_RPC_POLYGON_FULL_NODE=https://poly-archival.gateway.pokt.network/v1/lb/...
    # Read blocks 25,000,000 - 26,000,000 around when Enzyme was deployment on Polygon
    END_BLOCK=26000000 python scripts/enzyme/fetch-price-feeds.py

"""
import logging
import os

from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.price_feed import fetch_price_feeds
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate


def main():
    # Set up stdout logger
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON_FULL_NODE")
    assert json_rpc_url, f"You need to give JSON_RPC_POLYGON_FULL_NODE environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    end_block = os.environ.get("END_BLOCK")
    if end_block:
        end_block = int(end_block)
    else:
        end_block = web3.eth.block_number

    # Read Enzyme deployment from chain
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Set up multithreaded Polygon event reader
    reader = MultithreadEventReader(json_rpc_url, max_threads=16, notify=PrintProgressUpdate(), max_blocks_once=10_000)

    # Iterate through all events
    feeds = []
    for price_feed in fetch_price_feeds(
            deployment,
            start_block=POLYGON_DEPLOYMENT["deployed_at"],
            end_block=end_block,
            read_events=reader,
    ):
        feeds.append(price_feed)

    reader.close()

    print("Found Enzyme price feeds")
    for feed in feeds:
        print(f"   {feed}")


if __name__ == "__main__":
    main()
