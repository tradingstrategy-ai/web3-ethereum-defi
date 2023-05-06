"""Aave v3 deployer tests."""
import pytest
from _pytest.fixtures import FixtureRequest
from web3 import Web3, HTTPProvider

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture()
def anvil(request: FixtureRequest) -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    anvil = launch_anvil(
        gas_limit=15_000_000,
        port=8545,  # Must be localhost:8545 for Aave deployment
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    web3.middleware_onion.clear()
    # install_chain_middleware(web3)
    return web3


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[0]


@pytest.fixture()
def user(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[1]


def test_install_aave_deployer(aave_deployer: AaveDeployer):
    """Check Aave deployer git clone and npm install works"""
    assert aave_deployer.is_installed()


def test_deploy_aave(
    aave_deployer: AaveDeployer,
    anvil,
    web3,
):
    """Deploy Aave against local and check it's there."""
    aave_deployer.deploy_local(echo=True)
    assert aave_deployer.is_deployed(web3)


def test_deployment_smoke_test(
        aave_deployer: AaveDeployer,
        anvil,
        web3: Web3,
        deployer,
        user,
):
    """Deploy Aave against local and check something happens."""
    aave_deployer.deploy_local()

    assert web3.eth.block_number > 20

    faucet = aave_deployer.get_contract_at_address(web3, "periphery-v3/contracts/mocks/testnet-helpers/Faucet.sol/Faucet.json", "Faucet")
    usdc = aave_deployer.get_contract_at_address(web3, "core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json", "USDC")

    # Get some test money

    tx_hash = faucet.functions.mint(usdc.address, user, 500 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.functions.balanceOf(user).call() > 0

    # Get Aave Pool singleton address
    pool = aave_deployer.get_contract_at_address(web3, "core-v3/contracts/protocol/pool/Pool.sol/Pool.json", "Pool")
    assert pool.functions.POOL_REVISION().call() == 1
