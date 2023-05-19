"""Aave v3 deployment tests."""
import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture()
def user(web3) -> str:
    """User account"""
    return web3.eth.accounts[1]


def test_aave_v3_deployer_installation(aave_deployer):
    """Check Aave deployer git clone and npm install works"""
    assert aave_deployer.is_checked_out()
    assert aave_deployer.is_installed()


def test_deploy_aave_v3(
    aave_deployment: AaveDeployer,
    web3,
):
    """Deploy Aave against local Anvil and check it's there."""
    assert aave_deployment.is_deployed(web3)


def test_aave_v3_deployment_smoke_test(
    aave_deployment: AaveDeployer,
    web3: Web3,
    user: str,
    faucet: Contract,
    usdc: Contract,
):
    """Deploy Aave against local and check something happens."""
    # assert web3.eth.block_number > 20
    assert usdc.functions.balanceOf(user).call() == 0

    # Get some test money
    tx_hash = faucet.functions.mint(usdc.address, user, 500 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.functions.balanceOf(user).call() == 500 * 10**6

    # Get Aave Pool singleton
    pool = aave_deployment.get_contract_at_address(web3, "Pool.json", "PoolProxy")
    assert pool.functions.POOL_REVISION().call() == 1


def test_aave_v3_deployment_smoke_test_2(
    user: str,
    usdc: Contract,
):
    """Check deployer properly resets between tests."""
    assert usdc.functions.balanceOf(user).call() == 0
