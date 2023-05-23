"""Test open and close short position with Aave v3"""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import borrow, repay, supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.price import UniswapV3PriceHelper
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.uniswap_v3.utils import encode_path, get_default_tick_range


@pytest.fixture()
def ausdc(web3, aave_deployment) -> Contract:
    """aToken for USDC on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "AToken.json", "aUSDC")


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
def uniswap_v3(web3, deployer, weth) -> UniswapV3Deployment:
    """Uniswap v3 deployment."""
    return deploy_uniswap_v3(web3, deployer, weth=weth, give_weth=None)


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


def print_current_balances(address, usdc, weth):
    print(
        f"""
    Current balance:
        USDC: {usdc.functions.balanceOf(address).call() / 1e6}
        WETH: {weth.functions.balanceOf(address).call() / 1e18}
    """
    )


def test_aave_v3_short(
    web3: Web3,
    aave_v3_deployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    weth,
    weth_reserve,
    uniswap_v3,
    weth_usdc_pool,
    pool_trading_fee,
    deployer,
):
    """Test repay in Aave v3."""
    # starting with 10k
    print("\nStarting capital")
    print_current_balances(hot_wallet.address, usdc, weth)
    assert usdc.functions.balanceOf(hot_wallet.address).call() == 10_000 * 10**6

    # ----- 1. Open the short position by supply USDC as collateral to Aave v3
    print("> Step 1: Open the short position by supply USDC as collateral to Aave v3")
    # 10k USDC to use as collateral
    usdc_supply_amount = 10_000 * 10**6

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=usdc,
        amount=usdc_supply_amount,
    )

    tx = approve_fn.build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx = supply_fn.build_transaction({"from": hot_wallet.address, "gas": 350_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # verify aUSDC token amount in hot wallet
    assert ausdc.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount

    # verify hot wallet has 1k USDC left
    assert usdc.functions.balanceOf(hot_wallet.address).call() == 0
    print("Supply done!")
    print_current_balances(hot_wallet.address, usdc, weth)

    # ----- 2. Borrow tokens
    print("> Step 2: Borrow some WETH")
    borrow_amount = 2 * 10**18
    borrow_fn = borrow(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=weth,
        amount=borrow_amount,
    )
    tx = borrow_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert weth.functions.balanceOf(hot_wallet.address).call() == borrow_amount

    print("Borrow done")
    print_current_balances(hot_wallet.address, usdc, weth)

    # ----- 3. Sell tokens on Uniswap v3
    print("> Step 3: Sell WETH on Uniswap v3")
    max_slippage_bps = 5
    price_helper = UniswapV3PriceHelper(uniswap_v3)
    original_price = price_helper.get_amount_out(
        1 * 10**18,
        [weth.address, usdc.address],
        [pool_trading_fee],
        slippage=max_slippage_bps,
    )
    print("Current ETH price:", original_price / 1e6)
    assert original_price / 1e6 > 3900

    uniswap_v3_router = uniswap_v3.swap_router
    tx = weth.functions.approve(uniswap_v3_router.address, borrow_amount).build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    swap_fn = swap_with_slippage_protection(
        uniswap_v3_deployment=uniswap_v3,
        recipient_address=hot_wallet.address,
        base_token=usdc,
        quote_token=weth,
        pool_fees=[pool_trading_fee],
        amount_in=borrow_amount,
        max_slippage=max_slippage_bps,
    )
    tx = swap_fn.build_transaction({"from": hot_wallet.address, "gas": 350_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # we should get ~8k USDC after selling WETH
    assert usdc.functions.balanceOf(hot_wallet.address).call() / 1e6 == pytest.approx(7960.127505)
    assert weth.functions.balanceOf(hot_wallet.address).call() == 0

    print("Sell WETH done")
    print_current_balances(hot_wallet.address, usdc, weth)

    # ----- 4. Wait for price get lower
    print("> Step 4: Wait for ETH price plummet")
    # simulate price moves down due to ETH selling pressure
    weth.functions.approve(uniswap_v3_router.address, 100 * 10**18).transact({"from": deployer})
    tx_hash = uniswap_v3_router.functions.exactInput(
        (
            encode_path([weth.address, usdc.address], [pool_trading_fee]),
            deployer,
            FOREVER_DEADLINE,
            100 * 10**18,
            0,
        )
    ).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    price_helper = UniswapV3PriceHelper(uniswap_v3)
    new_price = price_helper.get_amount_out(
        1 * 10**18,
        [weth.address, usdc.address],
        [pool_trading_fee],
        slippage=max_slippage_bps,
    )
    print("New ETH price:", new_price / 1e6)
    assert new_price < original_price
    assert new_price / 1e6 < 3300

    print_current_balances(hot_wallet.address, usdc, weth)

    # ----- 5. Buy back tokens with USDC
    print("> Step 5: Buy back WETH")
    tx = usdc.functions.approve(uniswap_v3_router.address, 100_000 * 10**6).build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 200_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    swap_fn = swap_with_slippage_protection(
        uniswap_v3_deployment=uniswap_v3,
        recipient_address=hot_wallet.address,
        base_token=weth,
        quote_token=usdc,
        pool_fees=[pool_trading_fee],
        amount_in=6630 * 10**6,
        # TODO: amount_out doesn't work here for some reason
        # amount_out=2 * 10**18,
        max_slippage=max_slippage_bps,
    )
    tx = swap_fn.build_transaction({"from": hot_wallet.address, "gas": 350_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    print("Buy back WETH done")
    print_current_balances(hot_wallet.address, usdc, weth)

    # print(usdc.functions.balanceOf(hot_wallet.address).call() / 1e6)
    # print(weth.functions.balanceOf(hot_wallet.address).call() / 1e18)

    # ----- 6. Repay tokens to Aave
    print("> Step 6: Repay WETH loan in Aave")
    approve_fn, repay_fn = repay(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=weth,
        amount=MAX_AMOUNT,
    )

    # approve first
    tx = approve_fn.build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # then repay
    tx = repay_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    user_data = aave_v3_deployment.get_user_data(hot_wallet.address)
    assert user_data.total_debt_base == 0

    print("Repay done")
    print_current_balances(hot_wallet.address, usdc, weth)

    # ----- 7. Close position by withdraw all USDC in Aave
    print("> Step 7: Withdraw collateral in Aave, close position")
    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=usdc,
        amount=MAX_AMOUNT,
    )
    tx = withdraw_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    print("Withdraw done")

    # ----- 8. PnL
    print("> Step 8: Analyze PnL")
    print_current_balances(hot_wallet.address, usdc, weth)
