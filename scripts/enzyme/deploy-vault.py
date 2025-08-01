"""Deploy a new Enzyme vault with a generic adapter.

- This example deploys an Enzyme vault with custom policies and adapters.
  This is a different what you would be able eto deploy through Enzyme user interface.

- The adapter is configured to use the `GuardedGenericAdapter <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/in-house/src/GuardedGenericAdapter.sol>`__
  for trading from eth_defi package,
  allowing pass through any trades satisfying the `GuardV0 rules <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/guard>`__.

- Because we want multiple deployed smart contracts to be verified on Etherscan,
  this deployed uses a Forge-based toolchain and thus the script
  can be only run from the git checkout where submodules are included.

- The custom deposit and terms of service contracts are bound to the vault.

- Reads input from environment variables, so this can be used with scripting.

- The script can launch Anvil to simulate the deployment

The following Enzyme policies and activated to enable trading only via the generic adapter:

- cumulative_slippage_tolerance_policy (10% week)

- allowed_adapters_policy (only generic adapter)

- only_remove_dust_external_position_policy

- only_untrack_dust_or_priceless_assets_policy

- allowed_external_position_types_policy

Guard configuration:

- Guard ownership is *not* transferred from the deployer
  to the owner at the end of the script, as you likely need to configure


Example how to run this script to deploy a vault on Polygon:

.. code-block:: shell

    export SIMULATE=true
    # Set production=true flag - affects GuardedGenericAdapterDeployed event
    export PRODUCTION=true
    export FUND_NAME="TradingStrategy.ai ETH Breakpoint I"
    export FUND_SYMBOL=TS1
    export TERMS_OF_SERVICE=0xDCD7C644a6AA72eb2f86781175b18ADc30Aa4f4d
    export ASSET_MANAGER_ADDRESS=0xe747721f8C79A98d7A8dcE0dbd9f26B99E188137
    export OWNER_ADDRESS=0x238B0435F69355e623d99363d58F7ba49C408491
    # Whitelisted tokens for Polygon: WETH, WMATIC
    export WHITELISTED_TOKENS=0x7ceb23fd6bc0add59e62ac25578270cff1b9f619 0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270
    export PRIVATE_KEY=
    export JSON_RPC_URL=

    python scripts/enzyme/deploy-vault.py

"""

import logging
import os
import sys
from pprint import pformat

from eth_account import Account

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.enzyme.deployment import (
    ETHEREUM_DEPLOYMENT,
    POLYGON_DEPLOYMENT,
    EnzymeDeployment,
)
from eth_defi.enzyme.generic_adapter_vault import deploy_vault_with_generic_adapter
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    # Set up stdout logger
    setup_console_logging()

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_URL")
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    # Read the rest of the variables we use for deployment
    # See eth_defi.enzyme.generic_adapter_vault.deploy_generic_adapter_vault
    # for documentation.
    terms_of_service_address = os.environ["TERMS_OF_SERVICE"]
    private_key = os.environ["PRIVATE_KEY"]
    asset_manager_address = os.environ["ASSET_MANAGER_ADDRESS"]
    owner_address = os.environ["OWNER_ADDRESS"]
    fund_name = os.environ["FUND_NAME"]
    fund_symbol = os.environ["FUND_SYMBOL"]
    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    simulate = os.environ.get("SIMULATE", "").strip() == "true"
    production = os.environ.get("PRODUCTION", "").strip() == "true"
    uniswap_v2 = os.environ.get("UNISWAP_V2", "").strip() == "true"
    uniswap_v3 = os.environ.get("UNISWAP_V3", "").strip() == "true"

    if simulate:
        logger.info("Simulating deployment")
        anvil = launch_anvil(json_rpc_url)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
    else:
        logger.info("Production deployment")
        web3 = create_multi_provider_web3(json_rpc_url)
        anvil = None
        assert etherscan_api_key is not None, "You need Etherscan API key to verify deployed prod contracts"

    deployer = Account.from_key(private_key)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(deployer))

    # Build the list of whitelisted assets GuardV0 allows us to trade
    whitelisted_assets = []
    for token_address in os.environ.get("WHITELISTED_TOKENS", "").split():
        token_address = token_address.strip()
        if token_address:
            whitelisted_assets.append(fetch_erc20_details(web3, token_address))

    # Read Enzyme deployment and other configs (Uniswap router, etc) from chain
    match web3.eth.chain_id:
        case 137:
            enzyme = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT, deployer=deployer.address)
        case 1:
            enzyme = EnzymeDeployment.fetch_deployment(web3, ETHEREUM_DEPLOYMENT, deployer=deployer.address)
        case _:
            raise AssertionError(f"Chain {web3.eth.chain_id} not supported")

    terms_of_service = get_deployed_contract(
        web3,
        "terms-of-service/TermsOfService.json",
        terms_of_service_address,
    )
    terms_of_service.functions.latestTermsOfServiceVersion().call()  # Check ABI matches or crash

    assert owner_address.startswith("0x")
    assert asset_manager_address.startswith("0x")

    hot_wallet = HotWallet(deployer)
    hot_wallet.sync_nonce(web3)

    if simulate:
        logger.info("Simulation deployment")
    else:
        logger.info("Ready to deploy")
    logger.info("-" * 80)
    logger.info("Deployer hot wallet: %s", deployer.address)
    logger.info("Deployer balance: %f, nonce %d", hot_wallet.get_native_currency_balance(web3), hot_wallet.current_nonce)
    logger.info("Enzyme FundDeployer: %s", enzyme.contracts.fund_deployer.address)
    logger.info("USDC: %s", enzyme.usdc.address)
    logger.info("Terms of service: %s", terms_of_service.address)
    logger.info("Fund: %s (%s)", fund_name, fund_symbol)
    logger.info("Whitelisted assets: USDC and %s", ", ".join([a.symbol for a in whitelisted_assets]))
    if owner_address != deployer.address:
        logger.info("Ownership will be transferred to %s", owner_address)
    else:
        logger.warning("Ownership will be retained at the deployer %s", deployer.address)

    if asset_manager_address != deployer.address:
        logger.info("Asset manager is %s", asset_manager_address)
    else:
        logger.warning("No separate asset manager role set")

    logger.info("-" * 80)

    if not simulate:
        confirm = input("Ok [y/n]? ")
        if not confirm.lower().startswith("y"):
            print("Aborted")
            sys.exit(1)

    logger.info("Starting deployment")

    vault = deploy_vault_with_generic_adapter(
        enzyme,
        hot_wallet,
        asset_manager_address,
        owner_address,
        enzyme.usdc,
        terms_of_service,
        fund_name=fund_name,
        fund_symbol=fund_symbol,
        whitelisted_assets=whitelisted_assets,
        production=production,
    )

    if anvil:
        anvil.close()

    logger.warning("GuardV0 owner is still set to the deployer %s", deployer.address)
    logger.info("Vault deployed")
    logger.info("Vault info is:\n%s", pformat(vault.get_deployment_info()))


if __name__ == "__main__":
    main()
