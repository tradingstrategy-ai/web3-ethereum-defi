"""Enzyme deployment fixtures.

- Common fixtures used in all Enzyme based tests

- We need to set up a lot of stuff to ramp up Enzyme

"""
import pytest
from eth_defi.token import create_token
from eth_defi.uniswap_v2.deployment import deploy_uniswap_v2_like, UniswapV2Deployment
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract


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


@pytest.fixture
def enzyme_uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    deployment = deploy_uniswap_v2_like(web3, deployer)
    factory = deployment.factory
    assert factory.functions.allPairsLength().call() == 0

    assert deployment.router.functions.WETH().call() == deployment.weth.address
    assert deployment.router.functions.factory().call() == factory.address
    return deployment


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


@pytest.fixture
def weth(enzyme_uniswap_v2):
    return enzyme_uniswap_v2.weth


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


@pytest.fixture()
def mln(web3, deployer) -> Contract:
    """Mock MLN token.
    """
    token = create_token(web3, deployer, "Melon", "MLN", 5_000_000 * 10**18)
    return token


