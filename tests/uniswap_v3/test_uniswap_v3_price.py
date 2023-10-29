"""Test Uniswap v3 price calculation."""
import secrets
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.token import create_token, reset_default_token_cache
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.price import (
    UniswapV3PriceHelper,
    estimate_buy_received_amount,
    estimate_sell_received_amount,
    get_onchain_price,
)
from eth_defi.uniswap_v3.utils import get_default_tick_range

WETH_USDC_FEE_RAW = 3000
WETH_DAI_FEE_RAW = 3000


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

    # This test does not work with token cache
    reset_default_token_cache()

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
def hot_wallet_private_key() -> HexBytes:
    """Generate a private key"""
    return HexBytes(secrets.token_bytes(32))


@pytest.fixture()
def hot_wallet(eth_tester, hot_wallet_private_key) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    '"""
    # also add to eth_tester so we can use transact() directly
    eth_tester.add_account(hot_wallet_private_key.hex())
    return Account.from_key(hot_wallet_private_key)


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


@pytest.fixture()
def weth_usdc_uniswap_pool(web3, uniswap_v3, weth, usdc, deployer) -> Contract:
    """Mock WETH-USDC pool."""

    min_tick, max_tick = get_default_tick_range(WETH_USDC_FEE_RAW)

    pool_contract = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=WETH_USDC_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool_contract,
        amount0=10 * 10**18,  # 10 ETH liquidity
        amount1=17_000 * 10**18,  # 17000 USDC liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool_contract.address


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


def test_estimate_buy_price_for_cash(
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Estimate the price of buying 1650 USDC worth of ETH
    eth_received = estimate_buy_received_amount(
        uniswap_v3,
        weth.address,
        usdc.address,
        1650 * 10**18,
        WETH_USDC_FEE_RAW,
    )

    assert eth_received / (10**18) == pytest.approx(0.8822985189098446)

    # Calculate price of ETH as $ for our purchase
    price = (1650 * 10**18) / eth_received
    assert price == pytest.approx(Decimal(1870.1153460381145))

    # test verbose mode
    eth_received, block_number = estimate_buy_received_amount(
        uniswap_v3,
        weth.address,
        usdc.address,
        1650 * 10**18,
        WETH_USDC_FEE_RAW,
        verbose=True,
    )

    assert eth_received / (10**18) == pytest.approx(0.8822985189098446)
    assert block_number > 0


def test_estimate_sell_received_cash(
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Sell 50 ETH
    usdc_received = estimate_sell_received_amount(
        uniswap_v3,
        weth.address,
        usdc.address,
        50 * 10**18,
        WETH_USDC_FEE_RAW,
    )

    usdc_received_decimals = usdc_received / 10**18
    assert usdc_received_decimals == pytest.approx(14159.565580618213)

    # Calculate price of ETH as $ for our purchase
    # Pool only starts with 10 eth, and we are selling 50, so we should not expect to get a good price
    price = usdc_received / (50 * 10**18)
    assert price == pytest.approx(Decimal(283.19131161236425))

    # test verbose mode
    usdc_received, block_number = estimate_sell_received_amount(
        uniswap_v3,
        weth.address,
        usdc.address,
        50 * 10**18,
        WETH_USDC_FEE_RAW,
        verbose=True,
    )
    assert usdc_received / 1e18 == pytest.approx(14159.565580618213)
    assert block_number > 0


def test_get_onchain_price(web3, weth_usdc_uniswap_pool: str):
    """Test get onchain price of a pool."""

    price = get_onchain_price(
        web3,
        weth_usdc_uniswap_pool,
    )

    assert price == pytest.approx(Decimal(1699.9057541866793))

    reverse_price = get_onchain_price(
        web3,
        weth_usdc_uniswap_pool,
        reverse_token_order=True,
    )
    assert reverse_price == pytest.approx(Decimal(0.00058826790693372))
