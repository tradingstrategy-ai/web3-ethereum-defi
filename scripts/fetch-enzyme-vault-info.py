""""Read Enzyme vault info from Polygon.

Needs Polygon archival node.

Example:

.. code-block:: shell

    export JSON_RPC_URL=https://poly-archival.gateway.pokt.network/v1/lb/...

"""
import os

from web3 import HTTPProvider, Web3

from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, EnzymeDeployment


def main():
    json_rpc_url = os.environ.get("JSON_RPC_URL")
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))

    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    print(f"Chain {web3.eth.chain_id}, fetched Enzyme deployment with ComptrollerLib as {deployment.contracts.comptroller_lib.address}")

    # Randomly picked
    # https://app.enzyme.finance/vault/0x6c4a43d136d695a80bab48732df1be2571429b0c?network=polygon
    vault_address = "0x6c4a43d136d695a80bab48732df1be2571429b0c"

    vault =


if __name__ == "__main__":
    main()