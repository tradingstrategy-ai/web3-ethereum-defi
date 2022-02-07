"""Test Uniswap v2 liquidity provision and trading."""
from decimal import Decimal

import pytest
from eth_tester import EthereumTester
from web3 import Web3, EthereumTesterProvider
from web3.contract import Contract

from smart_contracts_for_testing.token import create_token
from smart_contracts_for_testing.uniswap_v2 import deploy_uniswap_v2_like, UniswapV2Deployment, deploy_trading_pair, \
    FOREVER_DEADLINE
from smart_contracts_for_testing.uniswap_v2_analysis import analyse_trade, TradeSuccess, TradeFail


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider) -> EthereumTester:
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


def test_analyse_trade_success(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
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

    analysis = analyse_trade(web3, uniswap_v2, tx_hash)
    assert isinstance(analysis, TradeSuccess)

    assert (1 / analysis.price) == pytest.approx(Decimal('1755.115346038114345242609866'))
    assert analysis.get_effective_gas_price_gwei() == 1


def test_analyse_trade_failed(eth_tester: EthereumTester, web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """Aanlyze reverted Uniswap v2 trade."""

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

    # Fail reason: Do not approve() enough USDC
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, 1).transact({"from": user_1})

    # We need to disable auto mine in order to test
    # revert messages properly
    eth_tester.disable_auto_mine_transactions()
    try:
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
            "from": user_1,
            # We need to pass explicit gas, otherwise
            # we get eth_tester.exceptions.TransactionFailed: execution reverted: TransferHelper: TRANSFER_FROM_FAILED
            # from eth_estimateGas
            "gas": 600_000,
        })

        eth_tester.mine_block()

        analysis = analyse_trade(web3, uniswap_v2, tx_hash)
        assert isinstance(analysis, TradeFail)
        assert analysis.get_effective_gas_price_gwei() == 1
    finally:
        eth_tester.enable_auto_mine_transactions()



