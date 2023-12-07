"""Uniswap v3 slippage and trade success tests."""
from decimal import Decimal

import pytest
from eth_tester import EthereumTester
from eth_typing import HexAddress
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.token import create_token, reset_default_token_cache
from eth_defi.trade import TradeFail, TradeSuccess
from eth_defi.uniswap_v3.analysis import analyse_trade_by_receipt
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE, MAX_TICK, MIN_TICK
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.utils import encode_path, get_default_tick_range


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider) -> EthereumTester:
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def user_1(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[1]


@pytest.fixture()
def user_2(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


@pytest.fixture()
def uniswap_v3(web3, deployer) -> UniswapV3Deployment:
    """Uniswap v2 deployment."""
    deployment = deploy_uniswap_v3(web3, deployer)
    return deployment


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 10_000_000 * 10**6, 6)
    return token


@pytest.fixture()
def weth(uniswap_v3: UniswapV3Deployment) -> Contract:
    """Mock WETH token."""
    return uniswap_v3.weth


@pytest.fixture()
def weth_usdc_fee() -> int:
    """Get fee for weth_usdc trading pool on Uniswap v3 (fake)"""
    return 3000


@pytest.fixture
def weth_usdc_pool(web3, deployer, uniswap_v3, weth, usdc, weth_usdc_fee) -> HexAddress:
    """ETH-USDC pool with 1.7M liquidity."""
    min_tick, max_tick = get_default_tick_range(weth_usdc_fee)

    pool_contract = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=weth_usdc_fee,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool_contract,
        amount0=1000 * 10**18,  # 1000 ETH liquidity
        amount1=1_700_000 * 10**6,  # 1.7M USDC liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )
    return pool_contract


def test_analyse_by_receipt(
    web3: Web3,
    deployer: str,
    user_1,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_pool: Contract,
    weth_usdc_fee: int,
):
    """Analyse a Uniswap v3 trade by receipt."""

    # See if we can fix the Github CI random fails with this
    reset_default_token_cache()

    router = uniswap_v3.swap_router

    # Give user_1 some cash to buy ETH and approve it on the router
    usdc_amount_to_pay = 500 * 10**6
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    encoded_path = encode_path(path, [weth_usdc_fee])

    tx_hash = router.functions.exactInput(
        (
            encoded_path,
            user_1,
            FOREVER_DEADLINE,
            10 * 10**6,
            0,
        )
    ).transact({"from": user_1})

    tx = web3.eth.get_transaction(tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    # user_1 has less than 500 USDC left to loses in the LP fees
    analysis = analyse_trade_by_receipt(web3, uniswap_v3, tx, tx_hash, receipt)
    assert isinstance(analysis, TradeSuccess)
    assert analysis.amount_out_decimals == 18
    assert analysis.amount_in_decimals == 6
    assert analysis.price == pytest.approx(Decimal(1699.9102484539058))
    assert analysis.get_effective_gas_price_gwei() == 1
    assert analysis.lp_fee_paid == pytest.approx(0.03)

    all_weth_amount = weth.functions.balanceOf(user_1).call()
    weth.functions.approve(router.address, all_weth_amount).transact({"from": user_1})

    # Perform the reverse swap WETH->USDC
    reverse_path = [weth.address, usdc.address]  # Path tell how the swap is routed
    tx_hash = router.functions.exactInput(
        (
            encode_path(reverse_path, [weth_usdc_fee]),
            user_1,
            FOREVER_DEADLINE,
            all_weth_amount - 1000,
            0,
        )
    ).transact({"from": user_1})

    tx = web3.eth.get_transaction(tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    # user_1 has less than 500 USDC left to loses in the LP fees
    analysis = analyse_trade_by_receipt(web3, uniswap_v3, tx, tx_hash, receipt)

    assert isinstance(analysis, TradeSuccess)
    assert analysis.price == pytest.approx(Decimal(1699.9102484539058))
    assert analysis.get_human_price(reverse_token_order=False) == pytest.approx(Decimal(1699.9102484539058))
    assert analysis.get_human_price(reverse_token_order=True) == pytest.approx(Decimal(1 / 1699.9102484539058))
    assert analysis.get_effective_gas_price_gwei() == 1
    assert analysis.amount_out_decimals == 6
    assert analysis.amount_in_decimals == 18
    assert analysis.lp_fee_paid == pytest.approx(1.7594014463335705e-05)
