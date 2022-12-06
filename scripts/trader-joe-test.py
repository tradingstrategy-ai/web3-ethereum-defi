"""Test Trader Joe compatibility.

Check manually that we can read Trader Joe's data.
"""

import os

from eth_defi.uniswap_v2.deployment import fetch_deployment
from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware


def main():
    json_rpc_url = os.environ["JSON_RPC_AVALANCHE"]

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    deployment = fetch_deployment(
        web3,
        factory_address="0x9Ad6C38BE94206cA50bb0d90783181662f0Cfa10",
        router_address="0x60aE616a2155Ee3d9A68541Ba4544862310933d4",
        init_code_hash="0x0bbca9af0511ad1a1da383135cf3a8d2ac620e549ef9f6ae3a4c33c2fed0af91",
        allow_different_weth_var=True,
    )

    print("Trader Joe deployment is", deployment)
    assert deployment.weth is None  # Cannot read this, different ABI

    # Check that pair resolution works
    # WAVAX / USDC
    pair, token0, token1 = deployment.pair_for("0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7", "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e")
    assert pair.lower() == "0xf4003f4efbe8691b60249e6afbd307abe7758adb"
    assert token0.lower() == "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"
    assert token1.lower() == "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"


if __name__ == "__main__":
    main()
