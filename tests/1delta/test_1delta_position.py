"""Test 1delta opening and closing positions using forked Polygon.

To run tests in this module:

.. code-block:: shell

    export JSON_RPC_POLYGON="https://rpc.ankr.com/polygon"
    pytest -k test_1delta_only_open_short_position

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
from eth_defi.aave_v3.loan import supply
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.position import close_short_position, open_short_position
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

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
    return fetch_erc20_details(web3, "0x625E7708f30cA75bfd92586e17077590C60eb4cD", contract_name="aave_v3/AToken.json")


@pytest.fixture
def weth(web3):
    """Get WETH on Polygon."""
    return fetch_erc20_details(web3, "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619")


@pytest.fixture
def vweth(web3):
    """Get vPolWETH on Polygon."""
    return fetch_erc20_details(web3, "0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", contract_name="aave_v3/VariableDebtToken.json")


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
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )


@pytest.fixture
def one_delta_deployment(web3, aave_v3_deployment) -> OneDeltaDeployment:
    return fetch_1delta_deployment(
        web3,
        aave_v3_deployment,
        # flash_aggregator_address="0x168B4C2Cc2df4635D521Aa1F8961DD7218f0f427",
        flash_aggregator_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        broker_proxy_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
    )


def _execute_tx(web3, hot_wallet, fn, gas=350_000):
    tx = fn.build_transaction({"from": hot_wallet.address, "gas": gas})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)


def _print_current_balances(address, usdc, weth, ausdc, vweth):
    print(
        f"""
    ------------------------
    Current balance:
        USDC: {usdc.contract.functions.balanceOf(address).call() / 1e6}
        aUSDC: {ausdc.contract.functions.balanceOf(address).call() / 1e6}
        WETH: {weth.contract.functions.balanceOf(address).call() / 1e18}
        vWETH: {vweth.contract.functions.balanceOf(address).call() / 1e18}
    ------------------------
    """
    )


def test_1delta_only_open_short_position(
    web3,
    hot_wallet,
    large_usdc_holder,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    print("> Step 1: approve tokens")
    trader = one_delta_deployment.flash_aggregator
    proxy = one_delta_deployment.broker_proxy

    for token in [
        usdc,
        weth,
        ausdc,
    ]:
        print(f"\tApproving unlimited allowance for 1delta trader on {token.name}")
        approve_fn = token.contract.functions.approve(trader.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

        approve_fn = token.contract.functions.approve(aave_v3_deployment.pool.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    # approve delegate the vToken
    for token in [
        vweth,
    ]:
        print(f"\tApproving delegation for 1delta broker proxy on {token.name}")
        approve_fn = token.contract.functions.approveDelegation(proxy.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    print("> Step 2: supply USDC as collateral to Aave v3")

    usdc_supply_amount = 10_000 * 10**6

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )

    _execute_tx(web3, hot_wallet, supply_fn)

    # verify aUSDC token amount in hot wallet
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount
    print("\tSupply done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    print("> Step 3: open short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        borrow_amount=weth_borrow_amount,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 600_000)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == weth_borrow_amount

    print("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_open_and_close_short_position(
    web3,
    hot_wallet,
    large_usdc_holder,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    print("> Step 1: approve tokens")
    trader = one_delta_deployment.flash_aggregator
    proxy = one_delta_deployment.broker_proxy

    for token in [
        usdc,
        weth,
        ausdc,
    ]:
        print(f"\tApproving unlimited allowance for 1delta trader on {token.name}")
        approve_fn = token.contract.functions.approve(trader.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

        approve_fn = token.contract.functions.approve(aave_v3_deployment.pool.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    # approve delegate the vToken
    for token in [
        vweth,
    ]:
        print(f"\tApproving delegation for 1delta broker proxy on {token.name}")
        approve_fn = token.contract.functions.approveDelegation(proxy.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    print("> Step 2: supply USDC as collateral to Aave v3")

    usdc_supply_amount = 10_000 * 10**6

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )

    _execute_tx(web3, hot_wallet, supply_fn)

    # verify aUSDC token amount in hot wallet
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount
    print("\tSupply done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    print("> Step 3: open short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        borrow_amount=weth_borrow_amount,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 600_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)
    # let's hope eth doesn't dip below 100$ anytime soon
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > usdc_supply_amount + 100 * 10**6

    print("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    print("> Step 4: close short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 800_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed without few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() < usdc_supply_amount

    print("\tClose position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)
