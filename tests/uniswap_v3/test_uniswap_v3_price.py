"""Test Uniswap v3 price calculation."""
import pytest
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.token import create_token
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.price import UniswapV3PriceHelper
from eth_defi.uniswap_v3.utils import get_default_tick_range


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
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
def uniswap_v3(web3, deployer) -> UniswapV3Deployment:
    """Uniswap v3 deployment."""
    deployment = deploy_uniswap_v3(web3, deployer)
    return deployment


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def dai(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "DAI", "DAI", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v3) -> Contract:
    """Mock WETH token."""
    return uniswap_v3.weth


def test_price_helper(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
):
    """Test price helper.

    Since the setup part is fairly slow, we test multiple input/output in the same test

    Based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts
    """
    # setup 2 pools
    fee = 3000
    pool1 = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=fee,
    )
    pool2 = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=usdc,
        token1=dai,
        fee=fee,
    )

    # add same liquidity amount to both pools as in SDK tests
    min_tick, max_tick = get_default_tick_range(fee)
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool1,
        amount0=100_000,
        amount1=100_000,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool2,
        amount0=120_000,
        amount1=100_000,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    price_helper = UniswapV3PriceHelper(uniswap_v3)

    # test get_amount_out, based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts#L394
    for slippage, expected_amount_out in [
        (0, 7004),
        (5 * 100, 6670),
        (200 * 100, 2334),
    ]:
        amount_out = price_helper.get_amount_out(
            10_000,
            [
                weth.address,
                usdc.address,
                dai.address,
            ],
            [fee, fee],
            slippage=slippage,
        )

        assert amount_out == expected_amount_out

    # test get_amount_in, based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts#L361
    for slippage, expected_amount_in in [
        (0, 15488),
        (5 * 100, 16262),
        (200 * 100, 46464),
    ]:
        amount_in = price_helper.get_amount_in(
            10_000,
            [
                weth.address,
                usdc.address,
                dai.address,
            ],
            [fee, fee],
            slippage=slippage,
        )

        assert amount_in == expected_amount_in
