"""Arbitrum test fixtures for Gains/Ostium testing."""
import os
from decimal import Decimal

import pytest

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=375_216_652)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture()
def anvil_arbitrum_fork_write(request) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]
    # open_pnl = "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
        unlocked_addresses=[usdc_whale],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3_write(anvil_arbitrum_fork_write):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork_write.json_rpc_url, retries=1)
    return web3


@pytest.fixture()
def usdc(web3_write) -> TokenDetails:
    web3 = web3_write
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture()
def test_user(web3_write, usdc):
    web3 = web3_write
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account