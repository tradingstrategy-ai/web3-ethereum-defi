"""Test WETH contract interactions."""

import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.provider.anvil import mine, fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE, fetch_erc20_details, BRIDGED_USDC_TOKEN, USDT_NATIVE_TOKEN, get_weth_contract
from eth_defi.trace import assert_transaction_success_with_explanation


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

CI = os.environ.get("CI") == "true"


@pytest.fixture()
def anvil_arbitrum_fork(
    request,
) -> AnvilLaunch:
    """Reset write state between tests"""

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


# Anvil is piece of crap
# ERROR tests/lagoon/test_lagoon_cowswap.py::test_cowswap_quote - AssertionError: Could not read block number from Anvil after the launch anvil: at http://localhost:27496, stdout is 0 bytes, stderr is 209 bytes
@pytest.mark.skipif(CI, reason="Flaky on CI")
def test_weth9(web3):
    """Test WETH9 wrapping on Arbitrum."""
    # https://arbiscan.io/address/0x82af49447d8a07e3bd95bd0d56f35241523fbab1
    weth_contract = get_weth_contract(web3)
    assert weth_contract.address == Web3.to_checksum_address("0x82af49447d8a07e3bd95bd0d56f35241523fbab1")

    tx = weth_contract.functions.deposit().transact(
        {
            "from": web3.eth.accounts[0],
            "value": 1 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx)

    weth = fetch_erc20_details(web3, weth_contract.address)
    assert weth.symbol == "WETH"
    assert weth.fetch_balance_of(web3.eth.accounts[0]) == Decimal(1)
