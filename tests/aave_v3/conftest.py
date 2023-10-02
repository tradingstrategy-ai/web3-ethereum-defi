import logging
from pathlib import Path

import pytest
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.provider.anvil import (
    AnvilLaunch,
    dump_state,
    launch_anvil,
    load_state,
    revert,
    snapshot,
)
from eth_defi.chain import install_chain_middleware

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def aave_deployer() -> AaveDeployer:
    """Set up Aave v3 deployer using git and npm.

    We use session scope, because this fixture is damn slow.
    """
    deployer = AaveDeployer()
    if not deployer.is_installed():
        deployer.install(echo=True)
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


_snapshot_id: int | None = None


@pytest.fixture(scope="session")
def aave_deployment_snapshot(web3, aave_deployer) -> AaveDeployer:
    """Deploy Aave once and save Anvil snapshot as a reset point.

    NOTE: to have newer Aave v3 deployment state, simply remove the state file and re-run the test
    """

    anvil_state = Path(__file__).resolve().parent / "aave_v3_deployment.anvilstate"

    if anvil_state.exists():
        logger.info("Found Aave state, load into Anvil")
        load_state(web3, anvil_state.read_text())
    else:
        logger.info("Aave state not found, start deploying Aave")
        aave_deployer.deploy_local(web3, echo=True)

        logger.info("Save Anvil state")
        anvil_state.write_text(dump_state(web3))

    global _snapshot_id
    if _snapshot_id is None:
        _snapshot_id = snapshot(web3)
        logger.info("Saved Anvil snapshot %d", _snapshot_id)

    return aave_deployer


@pytest.fixture()
def aave_deployment(web3, aave_deployment_snapshot) -> AaveDeployer:
    """Restore blockchain to the state of Aave deployment.

    Resets blockchain state between tests.
    """
    global _snapshot_id
    revert_result = revert(web3, _snapshot_id)
    assert revert_result, f"Snapshot revert failed %d {_snapshot_id}"
    logger.info("Reverted to snapshot %d", _snapshot_id)

    # Any revert snapshot destroys the snapshot, so we need to do this again
    _snapshot_id = snapshot(web3)
    logger.info("Resaved Anvil snapshot %d", _snapshot_id)

    return aave_deployment_snapshot


@pytest.fixture()
def deployer(web3) -> str:
    """Deployer account"""
    # Uses Hardhat/Foundry first derived account
    return web3.eth.accounts[0]


@pytest.fixture()
def faucet(web3, aave_deployment) -> Contract:
    """Faucet on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "Faucet.json", "Faucet")


@pytest.fixture()
def usdc(web3, aave_deployment) -> Contract:
    """USDC on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "MintableERC20.json", "USDC")


@pytest.fixture()
def weth(web3, aave_deployment) -> Contract:
    """WETH on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "WETH9Mocked.json", "WETH")
