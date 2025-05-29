"""Example script for opening and closing position on GMX.


"""

import logging
import os

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3


def main():
    SIMULATE = os.environ.get("SIMULATE") == "true"
    JSON_RPC_ARBITRUM = os.environ["JSON_RPC_ARBITRUM"]

    if SIMULATE:
        print("Simulation deployment with Anvil")
        anvil = fork_network_anvil(JSON_RPC_ARBITRUM)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
    else:
        print("Base production deployment")
        web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
        PRIVATE_KEY = os.environ["PRIVATE_KEY"]

    assert PRIVATE_KEY, "Private key must be set in environment variable PRIVATE_KEY"

    chain_id = web3.eth.chain_id



if __name__ == "__main__":
    main()