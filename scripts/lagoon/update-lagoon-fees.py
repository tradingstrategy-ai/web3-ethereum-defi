"""An example script to update a Lagoon vault's fees.

To run:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_BASE=...
    SIMULATE=true python scripts/lagoon/update-lagoon-fees.py
"""

import logging
import os
import sys

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.vault import VaultSpec
from eth_defi.lagoon.deployment import update_lagoon_vault_fees
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3


logging.basicConfig(level=logging.INFO, stream=sys.stdout)


def main():
    PRIVATE_KEY = os.environ["PRIVATE_KEY"]
    JSON_RPC_BASE = os.environ["JSON_RPC_BASE"]
    VAULT_ADDRESS = os.environ["VAULT_ADDRESS"]

    MANAGEMENT_RATE = int(os.environ.get("MANAGEMENT_RATE", 0))
    PERFORMANCE_RATE = int(os.environ.get("PERFORMANCE_RATE", 0))

    SIMULATE = os.environ.get("SIMULATE")

    deployer_wallet = HotWallet.from_private_key(PRIVATE_KEY)

    print(deployer_wallet.address)

    if SIMULATE:
        print("Simulation deployment with Anvil")
        anvil = fork_network_anvil(JSON_RPC_BASE)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
    else:
        print("Base production deployment")
        web3 = create_multi_provider_web3(JSON_RPC_BASE)

    chain_id = web3.eth.chain_id

    update_lagoon_vault_fees(
        web3=web3,
        deployer=deployer_wallet,
        vault_spec=VaultSpec(chain_id, VAULT_ADDRESS),
        management_rate=MANAGEMENT_RATE,
        performance_rate=PERFORMANCE_RATE,
    )

    print("Lagoon vault fees updated")


if __name__ == "__main__":
    main()
