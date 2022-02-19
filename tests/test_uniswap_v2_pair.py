"""Test Uniswap v2 liquidity provision and trading."""
from decimal import Decimal

import pytest
from web3 import Web3, EthereumTesterProvider
from web3.contract import Contract

from eth_hentai.abi import get_deployed_contract
from eth_hentai.token import create_token
from eth_hentai.uniswap_v2 import deploy_uniswap_v2_like, UniswapV2Deployment, deploy_trading_pair, \
    FOREVER_DEADLINE
from eth_hentai.uniswap_v2_fees import estimate_buy_quantity, estimate_sell_price, \
    estimate_sell_price_decimals, estimate_buy_price_decimals


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
def user_2(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


@pytest.fixture()
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    deployment = deploy_uniswap_v2_like(web3, deployer)
    return deployment


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    """Mock WETH token."""
    return uniswap_v2.weth


def test_create_no_liquidity_trading_pair(web3: Web3, deployer: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Deploy mock trading pair on mock Uniswap v2."""

    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        0,  # 10 ETH liquidity
        0,  # 17000 USDC liquidity
    )

    # Check the pair was successfully deployed
    assert uniswap_v2.factory.functions.allPairsLength().call() == 1
    assert pair_address.startswith("0x")
    # https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/uniswapv2/UniswapV2Pair.sol
    pair = get_deployed_contract(web3, "UniswapV2Pair.json", pair_address)
    assert pair.functions.kLast().call() == 0
    assert pair.functions.token0().call() == weth.address
    assert pair.functions.token1().call() == usdc.address

    token_a, token_b, timestamp = pair.functions.getReserves().call()
    assert token_a == 0


def test_create_trading_pair_with_liquidity(web3: Web3, deployer: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Deploy mock trading pair on mock Uniswap v2."""

    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    pair = get_deployed_contract(web3, "UniswapV2Pair.json", pair_address)
    token_a, token_b, timestamp = pair.functions.getReserves().call()

    # Check we got the liquidity
    assert token_a == 10 * 10**18
    assert token_b == 17_000 * 10**18


def test_swap(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """User buys WETH on Uniswap v2 using mock USDC."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router

    # Give user_1 some cash to buy ETH and approve it on the router
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    # https://docs.uniswap.org/protocol/V2/reference/smart-contracts/router-02#swapexacttokensfortokens
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({
        "from": user_1
    })

    # Check the user_1 received ~0.284 ethers
    assert weth.functions.balanceOf(user_1).call() / 1e18 == pytest.approx(0.28488156127668085)


def test_estimate_quantity(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Estimate quantity."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    # Estimate how much ETH we will receive for 500 USDC
    amount_eth = estimate_buy_quantity(
        uniswap_v2,
        weth,
        usdc,
        500*10**18,
    )
    assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)


def test_estimate_sell_price(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Estimate sell price."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of selling 1 ETH
    usdc_per_eth = estimate_sell_price(
        uniswap_v2,
        weth,
        usdc,
        1 * 10**18,
    )
    price_as_usd = usdc_per_eth / 1e18
    assert price_as_usd == pytest.approx(1693.2118677678354)


def test_estimate_sell_price_decimals(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Estimate sell price using the decimal friendly function."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of selling 1 ETH
    usdc_per_eth = estimate_sell_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
    )
    assert usdc_per_eth == pytest.approx(Decimal(1693.2118677678354))


def test_estimate_buy_price_decimals(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Estimate sell price using the decimal friendly function."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of buying 1 ETH
    usdc_per_eth = estimate_buy_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
    )
    assert usdc_per_eth == pytest.approx(Decimal(1706.82216820632059904))


def test_buy_sell_round_trip(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Buys some token, then sells it.

    Does a full round trip of trade and see how much money we lost.
    """

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router

    # Give user_1 500 USD to buy ETH
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({
        "from": user_1
    })

    all_weth_amount = weth.functions.balanceOf(user_1).call()
    weth.functions.approve(router.address, all_weth_amount).transact({"from": user_1})

    # Perform the reverse swap WETH->USDC
    reverse_path = [weth.address, usdc.address]  # Path tell how the swap is routed
    router.functions.swapExactTokensForTokens(
        all_weth_amount,
        0,
        reverse_path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({
        "from": user_1
    })

    # user_1 has less than 500 USDC left to loses in the LP fees
    usdc_left = usdc.functions.balanceOf(user_1).call() / (10.0**18)
    assert usdc_left == pytest.approx(497.0895)
