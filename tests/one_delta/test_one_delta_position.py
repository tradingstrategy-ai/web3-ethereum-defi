"""Test 1delta opening and closing positions using forked Polygon."""
import logging
import os
import shutil

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.aave_v3.loan import supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.position import (
    approve,
    close_short_position,
    open_short_position,
)
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.

    `To find large holder accounts, use <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245"))


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
    """Set up a Web3 provider instance with a lot of workarounds for flaky nodes."""
    web3 = create_multi_provider_web3(anvil_polygon_chain_fork)
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


@pytest.fixture
def wmatic(web3):
    """Get WMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270")


@pytest.fixture
def vwmatic(web3):
    """Get vPolMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x4a1c3aD6Ed28a636ee1751C69071f6be75DEb8B8", contract_name="aave_v3/VariableDebtToken.json")


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


def _print_current_balances(address, usdc, weth, ausdc, vweth, wmatic=None, vwmatic=None):
    output = f"""
    ------------------------
    Current balance:
        USDC: {usdc.contract.functions.balanceOf(address).call() / 1e6}
        aUSDC: {ausdc.contract.functions.balanceOf(address).call() / 1e6}
        WETH: {weth.contract.functions.balanceOf(address).call() / 1e18}
        vWETH: {vweth.contract.functions.balanceOf(address).call() / 1e18}
    """

    if wmatic and vwmatic:
        output += f"""    WMATIC: {wmatic.contract.functions.balanceOf(address).call() / 1e18}
        vWMATIC: {vwmatic.contract.functions.balanceOf(address).call() / 1e18}
    """

    output += "------------------------\n\n"

    logger.info(output)


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
    """Test supply collateral and open short position in the same tx using multicall."""
    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 2: open short position")

    usdc_supply_amount = 10_000 * 10**6
    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=weth_borrow_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(90_000 * 10**6)

    current_ausdc_balance = ausdc.contract.functions.balanceOf(hot_wallet.address).call()
    current_vweth_balance = vweth.contract.functions.balanceOf(hot_wallet.address).call()
    assert current_ausdc_balance > 11_000 * 10**6
    assert current_vweth_balance == pytest.approx(weth_borrow_amount)

    logger.info("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    # mine some blocks
    for i in range(1, 50):
        mine(web3)

    # check aToken and vToken balances should grow
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > current_ausdc_balance
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() > current_vweth_balance


def test_1delta_open_short_position_supply_separately(
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
    """Test supply collateral and open short position in 2 separate txs."""
    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 2: supply USDC as collateral to Aave v3")

    usdc_supply_amount = 10_000 * 10**6

    # supply USDC to Aave
    _, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )

    _execute_tx(web3, hot_wallet, supply_fn)

    # verify aUSDC token amount in hot wallet
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(usdc_supply_amount)
    logger.info("\tSupply done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 3: open short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=weth_borrow_amount,
        wallet_address=hot_wallet.address,
        do_supply=False,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 800_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(90_000 * 10**6)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > 11_000 * 10**6
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == weth_borrow_amount

    logger.info("\tOpen position done")

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
    """Test full flow of opening and closing short position using multicall:
    - supply collateral and open short position
    - close short position and withdraw collateral
    """
    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 2: open short position")

    usdc_supply_amount = 10_000 * 10**6
    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=weth_borrow_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)
    # let's hope eth doesn't dip below 100$ anytime soon
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > usdc_supply_amount + 100 * 10**6

    logger.info("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 4: close short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed without few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    assert 90_000 * 10**6 < usdc.contract.functions.balanceOf(hot_wallet.address).call() < 100_000 * 10**6

    logger.info("\tClose position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_open_and_close_short_position_separately(
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
    """Test full flow of opening and closing short position separately:
    - supply collateral
    - open short position
    - close short position
    - withdraw collateral
    """
    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 2: supply USDC as collateral to Aave v3")

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
    logger.info("\tSupply done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 3: open short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=weth_borrow_amount,
        wallet_address=hot_wallet.address,
        do_supply=False,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 600_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)
    # let's hope eth doesn't dip below 100$ anytime soon
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > usdc_supply_amount + 100 * 10**6

    logger.info("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 4: close short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        do_withdraw=False,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 600_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed without few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() < usdc_supply_amount

    logger.info("\tClose position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 4: Withdraw")

    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc.contract,
        amount=MAX_AMOUNT,
        wallet_address=hot_wallet.address,
    )

    _execute_tx(web3, hot_wallet, withdraw_fn)

    # all aUSDC is withdrawn back to hot wallet as USDC
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == 0
    assert 90_000 * 10**6 < usdc.contract.functions.balanceOf(hot_wallet.address).call() < 100_000 * 10**6

    logger.info("\tWithdraw done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)


@flaky.flaky(max_runs=3)
def test_1delta_open_and_close_short_positions_of_2_assets(
    web3,
    hot_wallet,
    large_usdc_holder,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
    wmatic,
    vwmatic,
):
    """Test open and close short positions of 2 different assets (ETH and MATIC)"""
    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=wmatic.contract,
        atoken=ausdc.contract,
        vtoken=vwmatic.contract,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

    logger.info("> Step 2.1: open short WETH position")

    usdc_supply_amount = 10_000 * 10**6
    weth_borrow_amount = 1 * 10**18
    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=weth_borrow_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)
    # let's hope eth doesn't dip below 100$ anytime soon
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > usdc_supply_amount + 100 * 10**6

    logger.info("> Step 2.2: open short WMATIC position")

    wmatic_borrow_amount = 1000 * 10**18
    swap_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=wmatic.contract,
        pool_fee=3000,
        collateral_amount=usdc_supply_amount,
        borrow_amount=wmatic_borrow_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vwmatic.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(wmatic_borrow_amount)
    # let's hope eth doesn't dip below 100$ anytime soon
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() > usdc_supply_amount + 100 * 10**6

    logger.info("\tOpen position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

    logger.info("> Step 3.1: close short WETH position")

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        do_withdraw=False,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0

    logger.info("> Step 3.2: close short WMATIC position")

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=wmatic.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        do_withdraw=False,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vwmatic.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed without few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() < usdc_supply_amount * 2

    logger.info("\tClose position done")

    _print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)
