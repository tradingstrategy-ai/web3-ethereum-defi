"""Test Uniswap v2 liquidity provision and trading."""

import pytest
from web3 import Web3, EthereumTesterProvider
from web3.contract import Contract

from smart_contracts_for_testing.token import create_token
from smart_contracts_for_testing.uniswap_v2 import deploy_uniswap_v2_like, UniswapV2Deployment, deploy_trading_pair, \
    FOREVER_DEADLINE
from smart_contracts_for_testing.uniswap_v2_analyse import analyse_trade


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
    token = create_token(web3, deployer, "USD Coin", "USDC", 10_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    """Mock WETH token."""
    return uniswap_v2.weth


def test_analyse_trade(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Aanlyze the Uniswap v2 trade."""

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
    tx_hash = router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({
        "from": user_1
    })

    receipt = web3.eth.get_transaction_receipt(tx_hash)

    analysis = analyse_trade(web3, uniswap_v2, tx_hash)
    import ipdb ; ipdb.set_trace()

