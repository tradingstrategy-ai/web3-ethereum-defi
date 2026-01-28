"""Manual test script for Blockscout contract verification.

Tests deploying and verifying a contract on Base mainnet using Blockscout.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/forge/test-blockscout-verification.py

Environment variables:

- ``JSON_RPC_BASE``: Base mainnet RPC URL
- ``DEPLOYER_PRIVATE_KEY``: Private key for the deployer account (must have ETH on Base)
"""

import logging
import os
from pathlib import Path

from eth_account import Account
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.foundry.forge import deploy_contract_with_forge
from eth_defi.hotwallet import HotWallet
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)


def main():
    setup_console_logging(default_log_level="info")

    json_rpc_url = os.environ.get("JSON_RPC_BASE")
    assert json_rpc_url, "JSON_RPC_BASE environment variable required"

    # Handle multi-provider URL format by taking the first URL
    if " " in json_rpc_url:
        json_rpc_url = json_rpc_url.split()[0]

    private_key = os.environ.get("DEPLOYER_PRIVATE_KEY")
    assert private_key, "DEPLOYER_PRIVATE_KEY environment variable required"

    web3 = Web3(HTTPProvider(json_rpc_url))
    install_chain_middleware(web3)

    deployer_account = Account.from_key(private_key)
    deployer = HotWallet(deployer_account)
    deployer.sync_nonce(web3)

    balance = web3.eth.get_balance(deployer.address)
    logger.info("Deployer: %s, balance: %s ETH", deployer.address, balance / 10**18)

    if balance < 0.00001 * 10**18:
        raise ValueError(f"Deployer account {deployer.address} has insufficient balance on Base mainnet")

    # Deploy a simple contract using Blockscout verification
    project_folder = Path(__file__).parent.parent.parent / "contracts" / "guard"
    assert project_folder.exists(), f"Project folder not found: {project_folder}"

    logger.info("Deploying GuardV0 with Blockscout verification on Base mainnet...")

    contract, tx_hash = deploy_contract_with_forge(
        web3,
        project_folder,
        Path("GuardV0.sol"),
        "GuardV0",
        deployer,
        constructor_args=[],
        verifier="blockscout",
        verifier_url="https://base.blockscout.com/api/",
        verify_retries=5,
        verbose=True,
    )

    logger.info("Contract deployed at: %s", contract.address)
    logger.info("Transaction hash: %s", tx_hash.hex())
    logger.info("Check verification at: https://base.blockscout.com/address/%s", contract.address)


if __name__ == "__main__":
    main()
