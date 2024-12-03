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
    reduce_short_position,
)
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

from .utils import _execute_tx, _print_current_balances

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)

logger = logging.getLogger(__name__)


def _open_short_position_multicall(
    *,
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """open short position using multicall"""

    logger.info("> Step 1: approve tokens")
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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
    _execute_tx(web3, hot_wallet, swap_fn, 1_0000_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(90_000 * 10**6)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(11625597245)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)

    logger.info("\tOpen position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_only_open_short_position(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test supply collateral and open short position in the same tx using multicall."""

    _open_short_position_multicall(
        web3=web3,
        hot_wallet=hot_wallet,
        one_delta_deployment=one_delta_deployment,
        aave_v3_deployment=aave_v3_deployment,
        usdc=usdc,
        ausdc=ausdc,
        weth=weth,
        vweth=vweth,
    )


def test_1delta_open_short_position_supply_separately(
    web3,
    hot_wallet,
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
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(11625597245)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(weth_borrow_amount)

    logger.info("\tOpen position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_open_and_close_short_position(
    web3,
    hot_wallet,
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

    _open_short_position_multicall(
        web3=web3,
        hot_wallet=hot_wallet,
        one_delta_deployment=one_delta_deployment,
        aave_v3_deployment=aave_v3_deployment,
        usdc=usdc,
        ausdc=ausdc,
        weth=weth,
        vweth=vweth,
    )

    logger.info("> Step 3: close short position")

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
    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(99990200748)

    logger.info("\tClose position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_open_and_close_short_position_separately(
    web3,
    hot_wallet,
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
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(usdc_supply_amount)
    logger.info("\tSupply done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(11625597245)

    logger.info("\tOpen position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

    logger.info("> Step 4: close short position")

    weth_borrow_amount = 1 * 10**18

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        withdraw_collateral_amount=0,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 600_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed within few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(9990200758)

    logger.info("\tClose position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)

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

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_increase_short_position(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test open then increase short position size."""
    _open_short_position_multicall(
        web3=web3,
        hot_wallet=hot_wallet,
        one_delta_deployment=one_delta_deployment,
        aave_v3_deployment=aave_v3_deployment,
        usdc=usdc,
        ausdc=ausdc,
        weth=weth,
        vweth=vweth,
    )

    logger.info("> Step 3: increase short position size")

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

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(80_000 * 10**6)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(23250132718)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(2 * weth_borrow_amount)

    logger.info("\tIncrease position done")
    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


def test_1delta_reduce_short_position_exact_out(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test open then reduce short position size using exact output (specifying how much borrowed token to reduce)"""
    _open_short_position_multicall(
        web3=web3,
        hot_wallet=hot_wallet,
        one_delta_deployment=one_delta_deployment,
        aave_v3_deployment=aave_v3_deployment,
        usdc=usdc,
        ausdc=ausdc,
        weth=weth,
        vweth=vweth,
    )

    logger.info("> Step 3: reduce short position size")

    # reduce position to half then withdraw corresponding collateral
    reduce_weth_borrow_amount = int(0.5 * 10**18)
    withdraw_collateral_amount = 1_000 * 10**6

    fn = reduce_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        reduce_borrow_amount=reduce_weth_borrow_amount,
        max_collateral_amount_in=MAX_AMOUNT,
        wallet_address=hot_wallet.address,
        withdraw_collateral_amount=withdraw_collateral_amount,
    )
    _execute_tx(web3, hot_wallet, fn, 1_000_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(91_000 * 10**6)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(9808032993)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(reduce_weth_borrow_amount)

    logger.info("\tReducing position done")
    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)


# FAILED tests/one_delta/test_one_delta_short_single_pair.py::test_1delta_reduce_short_position_exact_in - eth_defi.trace.TransactionAssertionError: Revert reason: execution reverted: revert: 32
@flaky.flaky()
def test_1delta_reduce_short_position_exact_in(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test open then reduce short position size using exact input (specifying how much collateral to reduce)"""
    _open_short_position_multicall(
        web3=web3,
        hot_wallet=hot_wallet,
        one_delta_deployment=one_delta_deployment,
        aave_v3_deployment=aave_v3_deployment,
        usdc=usdc,
        ausdc=ausdc,
        weth=weth,
        vweth=vweth,
    )

    logger.info("> Step 3: reduce short position size")

    # reduce position to half then withdraw corresponding collateral
    # reduce_weth_borrow_amount = int(0.5 * 10**18)
    reduce_collateral_amount = 1_000 * 10**6

    fn = reduce_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        reduce_collateral_amount=reduce_collateral_amount,
        min_borrow_amount_out=0,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, fn, 1_000_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(91_000 * 10**6)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(9625597255)
    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(388449638218292351)

    logger.info("\tReducing position done")
    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth)
