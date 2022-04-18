"""Test Uniswap v3 liquidity pool."""
import pytest
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.token import create_token
from eth_defi.uniswap_v3.constants import DEFAULT_FEES
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.utils import encode_sqrt_ratio_x96


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
def weth(uniswap_v3) -> Contract:
    """Mock WETH token."""
    return uniswap_v3.weth


def test_create_pool_wrong_fee(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    with pytest.raises(AssertionError) as e:
        deploy_pool(
            web3,
            deployer,
            deployment=uniswap_v3,
            token0=weth,
            token1=usdc,
            fee=10,
        )

        assert str(e) == "Default Uniswap v3 factory only allows 3 fee levels: 500, 3000, 10000"


@pytest.mark.parametrize("fee", DEFAULT_FEES)
def test_create_pool_no_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    fee: int,
):
    """Deploy mock pool on Uniswap v3 without initial liquidity."""
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=fee,
    )

    # Check the pool was successfully deployed
    assert pool.address.startswith("0x")
    assert uniswap_v3.factory.functions.getPool(weth.address, usdc.address, fee).call() == pool.address
    assert pool.functions.token0().call() == weth.address
    assert pool.functions.token1().call() == usdc.address
    assert pool.functions.fee().call() == fee

    # liquidity should be 0
    liquidity = pool.functions.liquidity().call()
    assert liquidity == 0


def test_create_pool_with_initial_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Deploy mock pool on Uniswap v3 with initial liquidity."""
    initial_amount0 = 100
    initial_amount1 = 200
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=3000,
        initial_amount0=initial_amount0,
        initial_amount1=initial_amount1,
    )

    # check if liquidity is there
    liquidity = pool.functions.liquidity().call()
    assert liquidity > 0

    # check if sqrt price is changed
    slot0 = pool.functions.slot0().call()
    assert slot0[0] == encode_sqrt_ratio_x96(amount0=initial_amount0, amount1=initial_amount1)
