import logging
from pathlib import Path

import pytest
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.chain import install_chain_middleware
from eth_defi.one_delta.deployer import OneDeltaDeployer
from eth_defi.provider.anvil import (
    AnvilLaunch,
    dump_state,
    launch_anvil,
    load_state,
    revert,
    snapshot,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def one_delta_deployer(web3) -> OneDeltaDeployer:
    """Set up Aave v3 deployer using git and npm.

    We use session scope, because this fixture is damn slow.
    """
    deployer = OneDeltaDeployer()
    if not deployer.is_installed():
        deployer.install(echo=True)
    else:
        deployer.deploy_local(web3, echo=True)
    return deployer


@pytest.fixture(scope="session")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    anvil = launch_anvil(
        gas_limit=25_000_000,
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture(scope="session")
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    return web3


def test_one_delta_deployer_installation(one_delta_deployer, web3):
    """Check 1delta deployer git clone and npm install works"""
    assert one_delta_deployer.is_checked_out()
    assert one_delta_deployer.is_installed()
    assert one_delta_deployer.is_deployed(web3)
