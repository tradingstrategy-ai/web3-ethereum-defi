"""Deploy a new Enzyme vault with a generic adapter.

- Deploys a tailored Enzyme vault with custom policies and adapters.
  This is a different what you would be able eto deploy through Enzyme user interface.

- The adapter is configured to use the generic adapter for trading from eth_defi package,

- The custom deposit and terms of service contracts are bound to the vault.

"""

import sys
import logging
import os

from eth_account import Account

from eth_defi.abi import get_deployed_contract
from eth_defi.enzyme.deployment import POLYGON_DEPLOYMENT, ETHEREUM_DEPLOYMENT, EnzymeDeployment
from eth_defi.enzyme.generic_adapter_vault import deploy_generic_adapter_vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from tradeexecutor.monkeypatch.web3 import construct_sign_and_send_raw_middleware

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

    web3 = create_multi_provider_web3(json_rpc_url)
    deployer = Account.from_key(private_key)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(deployer))

    # Read Enzyme deployment from chain
    match web3.eth.chain_id:
        case 137:
            enzyme = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
        case 1:
            enzyme = EnzymeDeployment.fetch_deployment(web3, ETHEREUM_DEPLOYMENT)
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

    balance = web3.eth.get_balance(deployer) / 10**18

    logger.info("Ready to deploy")
    logger.info("----------------")
    logger.info("Deployer is %s", deployer.address)
    logger.info("Our deployer balance is %f", balance)
    logger.info("FundDeployer is %s", enzyme.contracts.fund_deployer.address)
    logger.info("USDC is %s", enzyme.usdc.address)
    logger.info("Terms of service contract is %s", terms_of_service.address)
    if owner_address != deployer.address:
        logger.info("Ownership will be transferred to %s", owner_address)
    else:
        logger.info("WARNING! Ownership will be retained at the deployer %d", deployer.address)

    if asset_manager_address != deployer.address:
        logger.info("Asset manager is %s", asset_manager_address)
    else:
        logger.info("WARNING! No separate asset manager role set")

    confirm = input("Ok [y/n]?")
    if not confirm.lower().startswith("y"):
        print("Aborted")
        sys.exit(1)

    logger.info("Starting deployment")

    vault = deploy_generic_adapter_vault(
        enzyme,
        deployer.address,
        asset_manager_address,
        owner_address,
    )

    logger.info("Vault deployed")
    logger.info("Vault info is:\n%s", vault.get_deployment_info())


if __name__ == "__main__":
    main()
