"""Unit tests for Lagoon/Uniswap v3 LP position arb strategy."""

from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_function_selector
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import launch_anvil, AnvilLaunch
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN, create_token, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3, UniswapV3Deployment, deploy_uniswap_v3


@pytest.fixture()
def anvil(request, vault_owner, usdc_holder, asset_manager, valuation_manager) -> AnvilLaunch:
    """Create a new Anvil.

    :return: JSON-RPC URL for Web3
    """
    launch = launch_anvil(
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def deployer(web3) -> HexAddress:
    return web3.eth.accounts[0]


@pytest.fixture()
def arb_bot(web3) -> HexAddress:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> HexAddress:
    return web3.eth.accounts[2]


@pytest.fixture()
def user(web3) -> HexAddress:
    return web3.eth.accounts[3]


@pytest.fixture()
def usdc(web3, deployer) -> TokenDetails:
    contract = create_token(web3, deployer, "USD Coin", "USDC", 10_000_000 * 10**18, 6)
    return fetch_erc20_details(web3, contract.address)


@pytest.fixture()
def portfolio_token(web3, deployer) -> TokenDetails:
    """Create a traded token."""
    contract = create_token(web3, deployer, "Test Coin", "TEST", 10_000_000 * 10**18, 6)
    return fetch_erc20_details(web3, contract.address)


@pytest.fixture()
def portfolio_token_2(web3, deployer) -> TokenDetails:
    """Create a traded token."""
    contract = create_token(web3, deployer, "Test Coin 2", "TEST2", 10_000_000 * 10**18, 6)
    return fetch_erc20_details(web3, contract.address)


@pytest.fixture()
def uniswap_v3(
    web3,
    deployer,
    usdc,
    portfolio_token,
    portfolio_token_2,
) -> UniswapV3Deployment:
    """Deploy Uniswap v3.

    - Create pools TEST/USDC and TEST2 USDC
    """
    deployment = deploy_uniswap_v3(web3, deployer)
    deployment.factory.functions.enableFeeAmount(100, 1).transact({"from": deployer})

    return deployment



@pytest.fixture()
def lagoon_vault(web3, deployer, asset_manager):
    """Deploy a lagoon vault.

    - Configure asset maneger to be allowed to do trades
    """