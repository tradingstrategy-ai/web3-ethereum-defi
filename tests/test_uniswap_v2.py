"""Mock token deployment."""

import pytest
from web3 import Web3, EthereumTesterProvider

from smart_contracts_for_testing.uniswap_v2 import deploy_uniswap_v2_like


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


def test_deploy_uniswap_v2(web3: Web3, deployer: str):
    """Deploy mock Uniswap v2."""
    deployment = deploy_uniswap_v2_like(web3, deployer)
    factory = deployment.factory
    assert factory.functions.allPairsLength().call() == 0


def test_weth(web3: Web3, deployer: str):
    """Wrap some WETH."""
    deployment = deploy_uniswap_v2_like(web3, deployer)
    weth = deployment.weth
    weth.functions.deposit().transact({"from": deployer, "value": 5 * 10**18})
    assert weth.functions.balanceOf(deployer).call() == 5 * 10**18


def test_create_trading_pair(web3: Web3, deployer: str):
    """Deploy mock trading pair on mock Uniswap v2."""
    deployment = deploy_uniswap_v2_like(web3, deployer)
    factory = deployment.factory
    assert factory.functions.allPairsLength().call() == 0
