"""Test Uniswap v3 liquidity pool."""
from decimal import Decimal

import pytest
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_hentai.abi import get_deployed_contract
from eth_hentai.token import create_token
from eth_hentai.uniswap_v3.deployment import (
    UniswapV3Deployment,
    deploy_pool,
    deploy_uniswap_v3,
)


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
    """Uniswap v2 deployment."""
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


@pytest.fixture()
def fee() -> int:
    return 3_000


def test_create_pool(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    fee: int,
):
    """Deploy mock pool on mock Uniswap v3."""
    with pytest.raises(AssertionError) as e:
        pool_address = deploy_pool(
            web3,
            deployer,
            deployment=uniswap_v3,
            token_a=weth,
            token_b=usdc,
            fee=10,
        )

    pool_address = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token_a=weth,
        token_b=usdc,
        fee=fee,
    )

    # Check the pool was successfully deployed
    assert pool_address.startswith("0x")
    assert (
        uniswap_v3.factory.functions.getPool(weth.address, usdc.address, fee).call()
        == pool_address
    )

    pool = get_deployed_contract(web3, "uniswap_v3/UniswapV3Pool.json", pool_address)
    assert pool.functions.token0().call() == weth.address
    assert pool.functions.token1().call() == usdc.address
    assert pool.functions.fee().call() == fee
