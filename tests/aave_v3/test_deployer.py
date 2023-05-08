"""Aave v3 deployer tests."""
import pytest
from web3 import Web3

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture()
def user(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[1]


def test_aave_deployer_checkout_out():
    """Check Aave deployer git clone and npm install works"""
    deployer = AaveDeployer()
    assert deployer.is_checked_out()


def test_deploy_aave(
    aave_deployment: AaveDeployer,
    anvil,
    web3,
):
    """Deploy Aave against local Anvil and check it's there."""
    assert aave_deployment.is_deployed(web3)


def test_deployment_smoke_test(
    aave_deployment: AaveDeployer,
    web3: Web3,
    deployer,
    user,
):
    """Deploy Aave against local and check something happens."""

    assert web3.eth.block_number > 20

    faucet = aave_deployment.get_contract_at_address(web3, "periphery-v3/contracts/mocks/testnet-helpers/Faucet.sol/Faucet.json", "Faucet")
    usdc = aave_deployment.get_contract_at_address(web3, "core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json", "USDC")

    # Get some test money
    tx_hash = faucet.functions.mint(usdc.address, user, 500 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.functions.balanceOf(user).call() > 0

    # Get Aave Pool singleton
    pool = aave_deployment.get_contract_at_address(web3, "core-v3/contracts/protocol/pool/Pool.sol/Pool.json", "Pool")
    assert pool.functions.POOL_REVISION().call() == 1


def test_deployment_smoke_test(
    aave_deployment: AaveDeployer,
    web3: Web3,
    deployer,
    user,
):
    """Deploy Aave against local and check something happens."""

    assert web3.eth.block_number > 20

    faucet = aave_deployment.get_contract_at_address(web3, "periphery-v3/contracts/mocks/testnet-helpers/Faucet.sol/Faucet.json", "Faucet")
    usdc = aave_deployment.get_contract_at_address(web3, "core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json", "USDC")

    # Get some test money
    tx_hash = faucet.functions.mint(usdc.address, user, 500 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.functions.balanceOf(user).call() > 0

    # Get Aave Pool singleton
    pool = aave_deployment.get_contract_at_address(web3, "core-v3/contracts/protocol/pool/Pool.sol/Pool.json", "Pool")
    assert pool.functions.POOL_REVISION().call() == 1


def test_deployment_smoke_test_2(
    aave_deployment: AaveDeployer,
    web3: Web3,
    deployer,
    user,
):
    """Check deployer properly resets between tests."""
    usdc = aave_deployment.get_contract_at_address(web3, "core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json", "USDC")
    assert usdc.functions.balanceOf(user).call() == 0
