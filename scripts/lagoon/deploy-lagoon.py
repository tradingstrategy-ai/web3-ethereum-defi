"""An example script to deploy a Lagoon vault.

A quick script to test/simulate Lagoon vault deployment on any chain.

- Deploy a new Safe and Lagoon vault on any chain,
  using Lagoon v0.5.0 factory contract
- Allow automated trading on Uniswap v2 via TradingStrategyModuleV0,
  with configurations for Uniswap, ERC-4626 vault whitelisting and such
- Safe is 1-of-1 multisig with the deployer as the only cosigner
- You need to have a real deployer key with a balance,
  but in `SIMULATE` mode we will not use it and just do Anvil mainnet fork deployment

This **cannot** be used for product deployments, only for tests,
as it configures random Safe multisig cosigners.

To run:

.. code-block:: shell

    export PRIVATE_KEY=...
    export JSON_RPC_URL=$JSON_RPC_BINANCE
    SIMULATE=true python scripts/lagoon/deploy-lagoon.py
"""

import logging
import os
from typing import cast

from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN

from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3

from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


RANDO1 = "0xa7208b5c92d4862b3f11c0047b57a00Dc304c0f8"
RANDO2 = "0xbD35322AA7c7842bfE36a8CF49d0F063bf83a100"


def main():

    setup_console_logging(default_log_level="info")

    PRIVATE_KEY = os.environ["PRIVATE_KEY"]
    JSON_RPC_URL = os.environ["JSON_RPC_URL"]
    SIMULATE = os.environ.get("SIMULATE")
    ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY")

    # Comma separated list of ERC-4626 to whitelist
    VAULTS = os.environ.get("VAULTS")

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    chain_name = get_chain_name(web3.eth.chain_id).lower()

    logger.info(f"Connected to chain {chain_name}, last block is {web3.eth.block_number:,}")

    deployer_wallet = HotWallet.from_private_key(PRIVATE_KEY)
    asset_manager = deployer_wallet.address

    # Add some random multisig holders
    multisig_owners = [deployer_wallet.address, RANDO1, RANDO2]

    if SIMULATE:
        logger.info("Simulation deployment with Anvil")
        anvil = fork_network_anvil(JSON_RPC_URL)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
    else:
        logger.info("Production deployment")
        web3 = create_multi_provider_web3(JSON_RPC_URL)

    chain_id = web3.eth.chain_id

    assert chain_name in UNISWAP_V2_DEPLOYMENTS, "Unsupported chain in Uniswap v2 deployment data: " + chain_name

    uniswap_v2 = fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS[chain_name]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS[chain_name]["init_code_hash"],
    )

    if web3.eth.chain_id == 56:
        # Binance uses USDT,
        # also it does not have official Lagoon factory as the writing of this.
        underlying = USDT_NATIVE_TOKEN[chain_id]
        from_the_scratch = True
        factory_contract = True
    else:
        underlying = USDC_NATIVE_TOKEN[chain_id]
        factory_contract = True
        from_the_scratch = False

    parameters = LagoonDeploymentParameters(
        underlying=underlying,
        name="Test vault",
        symbol="TEST",
    )

    if VAULTS:
        erc_4626_vault_addresses = [Web3.to_checksum_address(a.strip()) for a in VAULTS.split(",")]
        erc_4626_vaults = []
        for addr in erc_4626_vault_addresses:
            logger.info("Resolving ERC-4626 vault at %s", addr)
            features = detect_vault_features(web3, addr)
            vault = cast(ERC4626Vault, create_vault_instance(web3, addr, features=features))
            assert vault.is_valid(), f"Invalid ERC-4626 vault at {addr}"
            logger.info("Preparing vault %s for whitelisting", vault.name)
            erc_4626_vaults.append(vault)
    else:
        erc_4626_vaults = None

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=len(multisig_owners) - 1,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_asset=True,
        erc_4626_vaults=erc_4626_vaults,
        factory_contract=factory_contract,
        use_forge=True,
        etherscan_api_key=ETHERSCAN_API_KEY,
        from_the_scratch=from_the_scratch,
    )

    logger.info(f"Lagoon vault deployed:\n{deploy_info.pformat()}")


if __name__ == "__main__":
    main()
