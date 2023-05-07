import os
import shutil
from pathlib import Path
from tempfile import gettempdir

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.anvil import AnvilLaunch, snapshot, revert, launch_anvil


@pytest.fixture(scope="session")
def aave_deployer() -> AaveDeployer:
    """Set up Aave v3 deployer using git and npm.

    We use session scope, because this fixture is damn slow.
    """
    deployer = AaveDeployer()
    deployer.install()
    return deployer


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    anvil = launch_anvil(
        port=8545,  # Must be localhost:8545 for Aave deployment
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture(scope="module")
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    web3.middleware_onion.clear()
    return web3


@pytest.fixture(scope="module")
def aave_deployment_snapshot(
        web3,
        aave_deployer,
) -> AaveDeployer:
    """Deploy Aave once and save Anvil snapshot as a reset point."""
    aave_deployer.deploy_local(echo=True)
    # Save state after deployment
    snapshot_id = snapshot(web3)
    assert snapshot_id == 0
    return aave_deployer


@pytest.fixture()
def aave_deployment(web3, aave_deployment_snapshot) -> AaveDeployer:
    """Restore blockchain to the state of Aave deployment.

    Resetes blockchain state between tests.
    """
    revert(web3, 0)
    return aave_deployment_snapshot


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account"""
    # Uses Hardhat/Foundry first derived account
    return web3.eth.accounts[0]
