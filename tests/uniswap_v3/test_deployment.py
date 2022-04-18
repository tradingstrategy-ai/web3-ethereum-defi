"""Mock token deployment."""
import pytest
from web3 import EthereumTesterProvider, Web3

from eth_defi.uniswap_v3.deployment import (
    deploy_uniswap_v3,
)


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


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


def test_deploy_uniswap_v3(web3: Web3, deployer: str):
    """Deploy mock Uniswap v3."""
    deployment = deploy_uniswap_v3(web3, deployer)

    # https://etherscan.io/address/0x1F98431c8aD98523631AE4a59f267346ea31F984#readContract
    factory = deployment.factory
    parameters = factory.functions.parameters().call()
    assert len(parameters) == 5
    assert parameters[4] == 0  # initial fee

    assert deployment.swap_router.functions.WETH9().call() == deployment.weth.address
    assert deployment.swap_router.functions.factory().call() == factory.address


def test_weth(web3: Web3, deployer: str):
    """Test wrapping WETH."""
    deployment = deploy_uniswap_v3(web3, deployer, give_weth=None)
    weth = deployment.weth
    weth.functions.deposit().transact({"from": deployer, "value": 5 * 10**18})
    assert weth.functions.balanceOf(deployer).call() == 5 * 10**18
