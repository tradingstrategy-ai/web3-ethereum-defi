""""Read Enzyme vault info from Polygon.

Manual test script to print out information for a single Enzyme vault.

Needs Polygon archival node.

Example:

.. code-block:: shell

    export JSON_RPC_URL=https://poly-archival.gateway.pokt.network/v1/lb/...
    python scripts/fetch-enzyme-vault-info.py

"""
import logging
import os
from functools import partial
from typing import cast

from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.reader import Web3EventReader, read_events


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    json_rpc_url = os.environ.get("JSON_RPC_URL")
    start_block = 26_050_000
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Randomly picked
    # https://app.enzyme.finance/vault/0x6c4a43d136d695a80bab48732df1be2571429b0c?network=polygon
    vault_address = "0x6c4A43d136d695a80bAB48732dF1Be2571429b0c"
    comptroller_contract, vault_contract = deployment.fetch_vault(vault_address)
    vault = Vault(vault_contract, comptroller_contract, deployment)
    print(f"Vault name: {vault.get_name()}")
    print(f"Denominated in: {vault.denomination_token}")
    raw_gross_asset_value = vault.get_gross_asset_value()
    print(f"Gross asset value: {vault.denomination_token.convert_to_decimals(raw_gross_asset_value):.2f} {vault.denomination_token.symbol}")

    def notify(
        current_block: int,
        start_block: int,
        end_block: int,
        chunk_size: int,
        total_events: int,
        last_timestamp: int,
        context,
    ):
        done = (current_block - start_block) / (end_block - start_block)
        print(f"Scanning blocks {current_block:,} - {current_block + chunk_size:,}, done {done * 100:.1f}%")

    reader: Web3EventReader = cast(Web3EventReader, partial(read_events, notify=notify, chunk_size=10_000, extract_timestamps=None))
    # reader: Web3EventReader = cast(Web3EventReader, read_events)
    deploy_event = vault.fetch_deployment_event(reader, start_block=start_block)
    print(f"Vault deployed in transaction {deploy_event['transactionHash']}, block {deploy_event['blockNumber']:,}")


if __name__ == "__main__":
    main()
