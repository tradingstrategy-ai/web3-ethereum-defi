""""Fetch policy manager information for all Eznyme vaults.

Needs Polygon full node. Get one from QuickNode.

Example:

.. code-block:: shell

    export JSON_RPC_POLYGON=https://poly-archival.gateway.pokt.network/v1/lb/...
    # Read blocks 25,000,000 - 26,000,000 around when Enzyme was deployment on Polygon
    END_BLOCK=26000000 python scripts/enzyme/fetch-price-feeds.py

"""
import csv
import datetime
import logging
import os
from typing import List

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.chainlink.round_data import fetch_chainlink_round_data
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.price_feed import fetch_price_feeds, EnzymePriceFeed, UnsupportedBaseAsset
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate
from eth_defi.token import fetch_erc20_details


def main():
    # Set up stdout logger
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON")
    assert json_rpc_url, f"You need to give JSON_RPC_POLYGO environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    start_block = 0

    end_block = os.environ.get("END_BLOCK")
    if end_block:
        end_block = int(end_block)
    else:
        end_block = web3.eth.block_number

    # Read Enzyme deployment from chain
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Set up multithreaded Polygon event reader.
    # Print progress to the console how many blocks there are left to read.
    reader = MultithreadEventReader(json_rpc_url, max_threads=16, notify=PrintProgressUpdate(), max_blocks_once=10_000)

    filter = Filter.create_filter(
        event_types=[deployment.contracts.fund_deployer.events.NewFundCreated],
    )

    with open(f"enzyme-vaults-chain-{web3.eth.chain_id}.csv", "wt") as f:
        csv_writer = csv.DictWriter(f, fieldnames=["vault", "created_block", "tx_hash", "tvl", "denomination_asset", "policies"])

        for event in reader(
                web3,
                start_block,
                end_block,
                filter=filter,
        ):
            # feeds.append(price_feed)
            vault_address = event["args"]["vaultProxy"]
            vault = Vault.fetch(web3, vault_address)

            tvl = vault.get_gross_asset_value()
            denomination_asset = vault.get_denomination_asset()

            policy_manager_address = vault.comptroller.functions.getPolicyManager().call()
            policy_manager = get_deployed_contract(web3, "enzyme/PolicyManager.json", policy_manager_address)
            policies = policy_manager.functions.getEnabledPoliciesForFund(vault.comptroller).call()

            csv_writer.writerow({
                "vault": vault.address,
                "created_block": event["blockNumber"],
                "tx_hash": event["transactionHash"],
                "tvl": tvl,
                "denomination_asset": denomination_asset,
                "policies": " ,".join(policies),
            })


    reader.close()

if __name__ == "__main__":
    main()
