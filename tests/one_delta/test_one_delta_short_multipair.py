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

from .utils import _execute_tx, _print_current_balances

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)

logger = logging.getLogger(__name__)


@flaky.flaky
def test_1delta_open_and_close_short_multipairs(
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
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=wmatic.contract,
        atoken=ausdc.contract,
        vtoken=vwmatic.contract,
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

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
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(11625597247)
    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(90000 * 10**6)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

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
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(22195468304)
    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(80000 * 10**6)

    logger.info("\tOpen position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

    logger.info("> Step 3.1: close short WETH position")

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        withdraw_collateral_amount=5_000 * 10**6,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vweth.contract.functions.balanceOf(hot_wallet.address).call() == 0
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(15560071815)

    # 5000 USDC should be withdrawn from Aave
    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(85000 * 10**6)

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)

    logger.info("> Step 3.2: close short WMATIC position")

    swap_fn = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=wmatic.contract,
        atoken=ausdc.contract,
        pool_fee=3000,
        wallet_address=hot_wallet.address,
        withdraw_collateral_amount=MAX_AMOUNT,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 1_000_000)

    assert vwmatic.contract.functions.balanceOf(hot_wallet.address).call() == 0

    # the short position is closed within few seconds so there is almost 0 interest accrued
    # and it costs 2 swaps to open and close the position (0.3% for each swap), so we end
    # up with slightly less USDC than we started with
    # all aUSDC should be withdrawn from Aave
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == 0
    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(99986764709)

    logger.info("\tClose position done")

    _print_current_balances(logger, hot_wallet.address, usdc, weth, ausdc, vweth, wmatic, vwmatic)
