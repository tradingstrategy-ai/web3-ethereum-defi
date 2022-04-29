"""Test Uniswap v2 liquidity provision and trading."""
import pytest
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.token import create_token
from eth_defi.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.liquidity import get_liquidity


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


def test_create_no_liquidity_trading_pair(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
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


def test_create_trading_pair_with_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
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


def test_swap(
    web3: Web3,
    deployer: str,
    user_1: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
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
    ).transact({"from": user_1})

    # Check the user_1 received ~0.284 ethers
    assert weth.functions.balanceOf(user_1).call() / 1e18 == pytest.approx(0.28488156127668085)


def test_get_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Fetch the liquidity of a Uniswap v2 pair."""

    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    liquidity_result = get_liquidity(web3, pair_address)

    assert liquidity_result.token0 == weth.address
    assert liquidity_result.token1 == usdc.address

    assert liquidity_result.get_liquidity_for_token(weth.address) == 10 * 10**18
    assert liquidity_result.block_number > 0
