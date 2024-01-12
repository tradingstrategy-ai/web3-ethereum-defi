"""Check guard against Uniswap v2 trades."""
import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract
from eth_defi.deploy import deploy_contract
from eth_defi.token import create_token
from eth_defi.uniswap_v2.deployment import deploy_uniswap_v2_like, deploy_trading_pair


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
def owner(web3) -> str:
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
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


@pytest.fixture()
def uniswap_v2(web3: Web3, usdc: Contract, deployer: str):
    """Deploy mock Uniswap v2."""
    uniswap_v2 = deploy_uniswap_v2_like(web3, deployer)
    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        uniswap_v2.weth,
        usdc,
        0,  # 10 ETH liquidity
        0,  # 17000 USDC liquidity
    )


@pytest.fixture()
def vault(web3: Web3, usdc: Contract, deployer: str) -> Contract:
    """Deploy mock Uniswap v2."""
    uniswap_v2 = deploy_uniswap_v2_like(web3, deployer)
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer)
    guard = get_contract(web3, "guard/GuardV0.json", vault.functions.guard.call())
    guard.functions.
    return vault
