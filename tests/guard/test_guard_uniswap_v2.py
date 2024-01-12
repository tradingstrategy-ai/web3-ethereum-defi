"""Check guard against Uniswap v2 trades."""
import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract
from eth_defi.deploy import deploy_contract
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import create_token
from eth_defi.uniswap_v2.deployment import deploy_uniswap_v2_like, deploy_trading_pair, UniswapV2Deployment, FOREVER_DEADLINE
from eth_defi.uniswap_v2.pair import fetch_pair_details, PairDetails


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
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


@pytest.fixture()
def uniswap_v2(web3: Web3, usdc: Contract, deployer: str) -> UniswapV2Deployment:
    """Deploy mock Uniswap v2."""
    uniswap_v2 = deploy_uniswap_v2_like(web3, deployer)


@pytest.fixture()
def vault(web3: Web3, usdc: Contract, deployer: str, owner: str, asset_manager: str) -> Contract:
    """Deploy mock Uniswap v2."""
    uniswap_v2 = deploy_uniswap_v2_like(web3, deployer)
    weth = uniswap_v2.weth
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer)
    vault.functions.transferOwnership(owner).transact({"from": deployer})
    vault.functions.updateAssetManager(asset_manager).transact({"from": owner})
    guard = get_contract(web3, "guard/GuardV0.json", vault.functions.guard.call())
    guard.functions.whitelistUniswapV2Router(uniswap_v2.router.address).transact({"from": deployer})
    guard.functions.whitelistToken(usdc.address)
    guard.functions.whitelistToken(weth.address)
    return vault


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    return uniswap_v2.weth


@pytest.fixture()
def weth_usdc_pair(uniswap_v2, weth, usdc, deployer) -> PairDetails:
    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**6,  # 17000 USDC liquidity
    )
    return fetch_pair_details(web3, pair_address)


def test_guard_can_trade_uniswap_v2(
    uniswap_v2: UniswapV2Deployment,
    owner: str,
    asset_manager: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract
):
    usdc_amount = 10_000 ** 10**6
    usdc.functions.transfer(vault.address, 10_000).transact({"from": deployer})

    path = [usdc.address, weth.address]

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})







