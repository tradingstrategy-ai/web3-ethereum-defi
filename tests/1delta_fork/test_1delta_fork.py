"""Test transaction decoding.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_decode_tx

"""
import logging
import os
import shutil

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.position import open_short_position, supply
from eth_defi.one_delta.utils import encode_path
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.tx import decode_signed_transaction

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)


@pytest.fixture(scope="module")
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.

    `To find large holder accounts, use <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x5a52E96BAcdaBb82fd05763E25335261B270Efcb"))


@pytest.fixture()
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_usdc_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_polygon_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_polygon_chain_fork))
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture
def usdc(web3):
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")


@pytest.fixture
def ausdc(web3):
    """Get aPolUSDC on Polygon."""
    return fetch_erc20_details(web3, "0x625E7708f30cA75bfd92586e17077590C60eb4cD")


@pytest.fixture
def weth(web3):
    """Get WETH on Polygon."""
    return fetch_erc20_details(web3, "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619")


@pytest.fixture
def vweth(web3):
    """Get vPolWETH on Polygon."""
    return fetch_erc20_details(web3, "0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351")


@pytest.fixture(scope="module")
def user_1() -> LocalAccount:
    """Create a test account."""
    return Account.create()


@pytest.fixture
def hot_wallet(web3, user_1, usdc, large_usdc_holder) -> HotWallet:
    """Hot wallet."""
    assert isinstance(user_1, LocalAccount)
    wallet = HotWallet(user_1)
    wallet.sync_nonce(web3)

    # give hot wallet some native token and USDC
    web3.eth.send_transaction(
        {
            "from": large_usdc_holder,
            "to": wallet.address,
            "value": 100 * 10**18,
        }
    )

    usdc.contract.functions.transfer(
        wallet.address,
        100_000 * 10**6,
    ).transact({"from": large_usdc_holder})

    return wallet


@pytest.fixture
def aave_v3_deployment(web3):
    return fetch_aave_deployment(
        web3,
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        "0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )


@pytest.fixture
def one_delta_deployment(web3, aave_v3_deployment):
    return fetch_1delta_deployment(
        web3,
        aave_v3_deployment,
        "0x168B4C2Cc2df4635D521Aa1F8961DD7218f0f427",
        "0x892e4a7d578Be979E5329655949fC56781eEFdb0",
        "0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
    )


# @pytest.fixture()
# def aave_v3_usdc_reserve() -> AaveToken:
#     return AAVE_V3_NETWORKS["polygon"].token_contracts["USDC"]


def _execute_tx(web3, hot_wallet, fn, gas=350_000):
    tx = fn.build_transaction({"from": hot_wallet.address, "gas": gas})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)


def test_1delta_fork_open_short(
    web3,
    hot_wallet,
    large_usdc_holder,
    one_delta_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test that the deposit in Aave v3 is correctly registered and the corresponding aToken is received."""
    usdc_supply_amount = 100 * 10**6

    print("> Step 1: supply USDC as collateral to Aave v3 via 1delta")

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        one_delta_deployment=one_delta_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )

    _execute_tx(web3, hot_wallet, approve_fn)
    _execute_tx(web3, hot_wallet, supply_fn)

    print("> Step 2: approve everything")

    # approve everything

    # verify aUSDC token amount in hot wallet
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount

    trader = one_delta_deployment.flash_aggregator
    manager = one_delta_deployment.manager
    proxy = one_delta_deployment.broker_proxy

    for token in [
        usdc,
        weth,
        ausdc,
    ]:
        approve_fn = token.contract.functions.approve(trader.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    # approve_fn = vweth.contract.functions.approveDelegation(proxy.address, MAX_AMOUNT)
    # _execute_tx(web3, hot_wallet, approve_fn)

    approve_fn = usdc.contract.functions.approve(one_delta_deployment.aave_v3.pool.address, MAX_AMOUNT)
    _execute_tx(web3, hot_wallet, approve_fn)

    print("> Step 3: open position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        borrow_amount=weth_borrow_amount,
    )
    _execute_tx(web3, hot_wallet, swap_fn)

    print("Open position done")
    # print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)
