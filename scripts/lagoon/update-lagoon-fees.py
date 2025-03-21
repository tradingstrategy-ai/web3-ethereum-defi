"""An example script to update a Lagoon vault's fees.

To run:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_BASE=...
    export VAULT_ADDRESS=...
    export MANAGEMENT_RATE=...
    export PERFORMANCE_RATE=...
    python scripts/lagoon/update-lagoon-fees.py
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
    PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
    DEPLOYER_ADDRESS = os.environ.get("DEPLOYER_ADDRESS")
    JSON_RPC_BASE = os.environ["JSON_RPC_BASE"]
    VAULT_ADDRESS = os.environ["VAULT_ADDRESS"]

    MANAGEMENT_RATE = int(os.environ.get("MANAGEMENT_RATE", 0))
    PERFORMANCE_RATE = int(os.environ.get("PERFORMANCE_RATE", 0))

    assert DEPLOYER_ADDRESS or PRIVATE_KEY, "Either PRIVATE_KEY or DEPLOYER_ADDRESS must be set"

    if PRIVATE_KEY:
        deployer_wallet = HotWallet.from_private_key(PRIVATE_KEY)
    else:
        deployer_wallet = DEPLOYER_ADDRESS

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    chain_id = web3.eth.chain_id

    tx_data = update_lagoon_vault_fees(
        web3=web3,
        deployer=deployer_wallet,
        vault_spec=VaultSpec(chain_id, VAULT_ADDRESS),
        management_rate=MANAGEMENT_RATE,
        performance_rate=PERFORMANCE_RATE,
    )

    if tx_data:
        print(f"Use Gnosis Safe to build the transaction with this data: {tx_data}")
    else:
        print(f"Lagoon vault fees updated to: management {MANAGEMENT_RATE} & performance {PERFORMANCE_RATE}")


if __name__ == "__main__":
    main()
