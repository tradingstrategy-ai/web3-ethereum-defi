"""Mock token deployment."""

import pytest
from web3 import EthereumTesterProvider, Web3

from eth_defi.uniswap_v2.deployment import deploy_uniswap_v2_like, fetch_deployment


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

    assert deployment.router.functions.WETH().call() == deployment.weth.address
    assert deployment.router.functions.factory().call() == factory.address


def test_weth(web3: Web3, deployer: str):
    """Wrap some WETH."""
    deployment = deploy_uniswap_v2_like(web3, deployer, give_weth=False)
    weth = deployment.weth
    weth.functions.deposit().transact({"from": deployer, "value": 5 * 10**18})
    assert weth.functions.balanceOf(deployer).call() == 5 * 10**18


def test_fetch_deployment(web3: Web3, deployer: str):
    """Reserve Uniswap deployment from on-chain data."""
    deployment = deploy_uniswap_v2_like(web3, deployer, give_weth=False)
    fetched = fetch_deployment(web3, deployment.factory.address, deployment.router.address)
    assert fetched.init_code_hash == deployment.init_code_hash
