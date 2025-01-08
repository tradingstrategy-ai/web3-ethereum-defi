"""An example script to deploy a Lagoon vault.

- Deploy new Safe and Lagoon vault on Base
- Allow automated trading on Uniswap v2 via TradingStrategyModuleV0
- Safe is 1-of-1 multisig with the deployer as the only cosigner

To run:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_BASE=...
    SIMULATE=true python scripts/lagoon/deploy-lagoon.py
"""
import logging
import os
import sys

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN

from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

RANDO1 = "0xa7208b5c92d4862b3f11c0047b57a00Dc304c0f8"
RANDO2 = "0xbD35322AA7c7842bfE36a8CF49d0F063bf83a100"


def main():
    PRIVATE_KEY = os.environ["PRIVATE_KEY"]
    JSON_RPC_BASE = os.environ["JSON_RPC_BASE"]
    SIMULATE = os.environ.get("SIMULATE")

    deployer_wallet = HotWallet.from_private_key(PRIVATE_KEY)
    deployer = deployer_wallet.account
    asset_manager = deployer.address
    # Add some random multisig holders
    multisig_owners = [deployer.address, RANDO1, RANDO2]

    if SIMULATE:
        print("Simulation deployment with Anvil")
        anvil = fork_network_anvil(JSON_RPC_BASE)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
    else:
        print("Base production deployment")
        web3 = create_multi_provider_web3(JSON_RPC_BASE)

    chain_id = web3.eth.chain_id

    uniswap_v2 = fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Test vault",
        symbol="TEST",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=len(multisig_owners) - 1,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_asset=True,
    )

    print(f"Lagoon vault deployed:\n{deploy_info.pformat()}")


if __name__ == "__main__":
    main()