"""Test 1delta lending functions using forked Polygon."""
import logging
import os
import shutil

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.lending import supply, withdraw
from eth_defi.one_delta.position import approve
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


def test_one_delta_supply(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test supply to Aave via 1delta proxy"""
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    wallet_original_balance = 100_000 * 10**6
    usdc_supply_amount = 10_000 * 10**6

    supply_fn = supply(
        one_delta_deployment=one_delta_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, supply_fn, 500_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(wallet_original_balance - usdc_supply_amount)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(usdc_supply_amount)


def test_one_delta_withdraw(
    web3,
    hot_wallet,
    one_delta_deployment,
    aave_v3_deployment,
    usdc,
    ausdc,
    weth,
    vweth,
):
    """Test withdraw from Aave via 1delta proxy"""
    for fn in approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc.contract,
        borrow_token=weth.contract,
        atoken=ausdc.contract,
        vtoken=vweth.contract,
        aave_v3_deployment=aave_v3_deployment,
    ):
        _execute_tx(web3, hot_wallet, fn)

    wallet_original_balance = 100_000 * 10**6
    usdc_supply_amount = 10_000 * 10**6

    supply_fn = supply(
        one_delta_deployment=one_delta_deployment,
        token=usdc.contract,
        amount=usdc_supply_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, supply_fn, 500_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(wallet_original_balance - usdc_supply_amount)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(usdc_supply_amount)

    # test partial withdrawal
    usdc_partial_withdraw_amount = 4_000 * 10**6

    withdraw_fn = withdraw(
        one_delta_deployment=one_delta_deployment,
        token=usdc.contract,
        atoken=ausdc.contract,
        amount=usdc_partial_withdraw_amount,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, withdraw_fn, 500_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(wallet_original_balance - usdc_supply_amount + usdc_partial_withdraw_amount)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(usdc_supply_amount - usdc_partial_withdraw_amount)

    # test full withdrawal
    withdraw_fn = withdraw(
        one_delta_deployment=one_delta_deployment,
        token=usdc.contract,
        atoken=ausdc.contract,
        amount=MAX_AMOUNT,
        wallet_address=hot_wallet.address,
    )
    _execute_tx(web3, hot_wallet, withdraw_fn, 500_000)

    assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == pytest.approx(wallet_original_balance)
    assert ausdc.contract.functions.balanceOf(hot_wallet.address).call() == 0
