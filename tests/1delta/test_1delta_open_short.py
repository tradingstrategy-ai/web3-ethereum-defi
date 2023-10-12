"""Test open and close short position with 1delta + Aave v3"""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import borrow, repay, supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment, deploy_1delta
from eth_defi.one_delta.position import open_short_position
from eth_defi.one_delta.utils import encode_path
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, deploy_uniswap_v2_like
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.utils import get_default_tick_range


@pytest.fixture
def aave_v3_deployment(web3, aave_deployment):
    pool = aave_deployment.get_contract_at_address(web3, "Pool.json", "PoolProxy")

    data_provider = aave_deployment.get_contract_at_address(web3, "AaveProtocolDataProvider.json", "PoolDataProvider")

    oracle = aave_deployment.get_contract_at_address(web3, "AaveOracle.json", "AaveOracle")

    return AaveV3Deployment(
        web3=web3,
        pool=pool,
        data_provider=data_provider,
        oracle=oracle,
    )


@pytest.fixture()
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    return deploy_uniswap_v2_like(web3, deployer, give_weth=None)


@pytest.fixture()
def uniswap_v3(web3, deployer, weth) -> UniswapV3Deployment:
    """Uniswap v3 deployment."""
    return deploy_uniswap_v3(web3, deployer, weth=weth, give_weth=None)


@pytest.fixture
def one_delta_deployment(web3, deployer, aave_v3_deployment, uniswap_v3, uniswap_v2, weth):
    return deploy_1delta(
        web3,
        deployer,
        uniswap_v2,
        uniswap_v3,
        aave_v3_deployment,
        weth,
    )


@pytest.fixture()
def ausdc(web3, aave_deployment) -> Contract:
    """aToken for USDC on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "AToken.json", "aUSDC")


@pytest.fixture()
def vweth(web3, aave_deployment) -> Contract:
    """vToken for WETH on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "VariableDebtToken.json", "vWETH")


@pytest.fixture
def pool_trading_fee():
    return 3000


@pytest.fixture
def weth_usdc_pool(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    pool_trading_fee: int,
    faucet,
):
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=usdc,
        token1=weth,
        fee=pool_trading_fee,
    )

    faucet.functions.mint(weth.address, deployer, 2_000 * 10**18).transact()
    faucet.functions.mint(usdc.address, deployer, 4_000_001 * 10**6).transact()

    min_tick, max_tick = get_default_tick_range(pool_trading_fee)
    tx_receipt, *_ = add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=4_000_000 * 10**6,
        amount1=1_000 * 10**18,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )
    assert tx_receipt["status"] == 1

    return pool


@pytest.fixture
def hot_wallet(
    web3,
    usdc,
    faucet,
) -> HotWallet:
    """Hotwallet account."""
    hw = HotWallet(Account.create())
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # and USDC
    tx_hash = faucet.functions.mint(usdc.address, hw.address, 10_000 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    return hw


@pytest.fixture
def weth_reserve(
    web3,
    aave_v3_deployment,
    weth,
    faucet,
    deployer,
):
    """Seed WETH reserve with 100 WETH liquidity"""
    # give deployer some WETH
    tx_hash = faucet.functions.mint(weth.address, deployer, 100_000 * 10**18).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    # supply to WETH reserve
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=deployer,
        token=weth,
        amount=100 * 10**18,
    )
    approve_fn.transact({"from": deployer})
    tx_hash = supply_fn.transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.fixture
def usdc_reserve(
    web3,
    aave_v3_deployment,
    usdc,
    faucet,
    deployer,
):
    """Seed WETH reserve with 100 WETH liquidity"""
    # give deployer some WETH
    tx_hash = faucet.functions.mint(usdc.address, deployer, 100_000 * 10**6).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    # supply to WETH reserve
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=deployer,
        token=usdc,
        amount=100_000 * 10**6,
    )
    approve_fn.transact({"from": deployer})
    tx_hash = supply_fn.transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)


def print_current_balances(address, usdc, weth, ausdc, vweth):
    print(
        f"""
    Current balance:
        USDC: {usdc.functions.balanceOf(address).call() / 1e6}
        aUSDC: {ausdc.functions.balanceOf(address).call() / 1e6}
        WETH: {weth.functions.balanceOf(address).call() / 1e18}
        vWETH: {vweth.functions.balanceOf(address).call() / 1e18}
    """
    )


def _execute_tx(web3, hot_wallet, fn, gas=350_000):
    tx = fn.build_transaction({"from": hot_wallet.address, "gas": gas})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)


def test_1delta_short(
    web3: Web3,
    one_delta_deployment: OneDeltaDeployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    vweth,
    weth,
    weth_reserve,
    usdc_reserve,
    weth_usdc_pool,
    pool_trading_fee,
    deployer,
):
    """Test repay in Aave v3."""

    print(
        f"""Test setup:

Aave pool: {one_delta_deployment.aave_v3.pool.address}
1delta flash aggregator: {one_delta_deployment.flash_aggregator.address}
Uniswap v3 WETH/USDC pool: {weth_usdc_pool.address}

Hot wallet: {hot_wallet.address}

USDC: {usdc.address}
WETH: {weth.address}
aUSDC: {ausdc.address}
vWETH: {vweth.address}

    """
    )

    # starting with 10k
    print("\nStarting capital")
    print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)
    assert usdc.functions.balanceOf(hot_wallet.address).call() == 10_000 * 10**6

    # ----- 1. Open the short position by supply USDC as collateral to Aave v3
    print("> Step 1: supply USDC as collateral to Aave v3 via 1delta")
    # 10k USDC to use as collateral
    usdc_supply_amount = 10_000 * 10**6

    # supply USDC to Aave
    approve_fn, supply_fn = open_short_position(
        one_delta_deployment=one_delta_deployment,
        wallet_address=hot_wallet.address,
        token=usdc,
        amount=usdc_supply_amount,
    )

    _execute_tx(web3, hot_wallet, approve_fn)
    _execute_tx(web3, hot_wallet, supply_fn)

    # verify aUSDC token amount in hot wallet
    assert ausdc.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount

    # verify hot wallet has 1k USDC left
    assert usdc.functions.balanceOf(hot_wallet.address).call() == 0
    print("Supply done!")
    print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)

    trader = one_delta_deployment.flash_aggregator
    manager = one_delta_deployment.manager
    proxy = one_delta_deployment.proxy

    print("> Step 2: approve everything")

    # approve everything
    for token in [
        usdc,
        weth,
        # ausdc,
    ]:
        approve_fn = token.functions.approve(trader.address, MAX_AMOUNT)
        _execute_tx(web3, hot_wallet, approve_fn)

    approve_fn = vweth.functions.approveDelegation(proxy.address, MAX_AMOUNT)
    _execute_tx(web3, hot_wallet, approve_fn)

    approve_fn = usdc.functions.approve(one_delta_deployment.aave_v3.pool.address, MAX_AMOUNT)
    _execute_tx(web3, hot_wallet, approve_fn)

    manager.functions.addAToken(usdc.address, ausdc.address).transact({"from": deployer})
    manager.functions.addVToken(weth.address, vweth.address).transact({"from": deployer})

    # this step doesn't quite work
    manager.functions.approveAAVEPool([usdc.address, weth.address]).transact({"from": deployer})

    print("> Step 3: open position")

    path = encode_path(
        [
            weth.address,
            usdc.address,
        ],
        [pool_trading_fee],
        [6],  # open position
        [1],  # pid of uniswap v3
        2,  # variable borrow
    )

    amount_in = int(0.5 * 10**18)
    min_amount_out = 0  # TODO: improve later

    swap_fn = trader.functions.flashSwapExactIn(
        amount_in,
        min_amount_out,
        path,
    )
    _execute_tx(web3, hot_wallet, swap_fn, 350_000)

    print("Open position done")
    print_current_balances(hot_wallet.address, usdc, weth, ausdc, vweth)
