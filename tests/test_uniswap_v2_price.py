import secrets
from decimal import Decimal
from re import T

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3._utils.transactions import fill_nonce
from web3.contract import Contract

from eth_hentai.token import create_token
from eth_hentai.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_hentai.uniswap_v2.fees import (
    UniswapV2FeeCalculator,
    estimate_buy_price_decimals,
    estimate_buy_quantity,
    estimate_sell_price,
    estimate_sell_price_decimals,
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
def user_2(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


@pytest.fixture()
def hot_wallet_private_key() -> HexBytes:
    """Generate a private key"""
    return HexBytes(secrets.token_bytes(32))


@pytest.fixture()
def hot_wallet(eth_tester, hot_wallet_private_key) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    '"""
    # also add to eth_tester so we can use transact() directly
    eth_tester.add_account(hot_wallet_private_key.hex())
    return Account.from_key(hot_wallet_private_key)


@pytest.fixture()
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    return deploy_uniswap_v2_like(web3, deployer)


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    """Mock WETH token."""
    return uniswap_v2.weth


def test_get_amount_in():
    assert UniswapV2FeeCalculator.get_amount_in(100, 1000, 1000) == 112
    assert UniswapV2FeeCalculator.get_amount_in(100, 10000, 10000) == 102
    assert UniswapV2FeeCalculator.get_amount_in(100, 10000, 10000, slippage=1000) == 113


def test_get_amount_out():
    assert UniswapV2FeeCalculator.get_amount_out(100, 1000, 1000) == 90
    assert UniswapV2FeeCalculator.get_amount_out(100, 10000, 10000) == 98
    assert UniswapV2FeeCalculator.get_amount_out(100, 1000, 1000, slippage=500) == 86


def test_swap_price_from_hot_wallet(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to buy WETH on Uniswap v2 using mock USDC."""

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
    hw_address = hot_wallet.address

    # Give hot wallet some cash to buy ETH (also some ETH as well to sign tx)
    # and approve it on the router
    web3.eth.send_transaction(
        {"from": deployer, "to": hw_address, "value": 1 * 10**18}
    )
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact(
        {"from": hw_address}
    )

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]
    tx = router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        hw_address,
        FOREVER_DEADLINE,
    ).buildTransaction({"from": hw_address})

    # prepare and sign tx
    tx = fill_nonce(web3, tx)
    signed = hot_wallet.sign_transaction(tx)

    # estimate the quantity before sending transaction
    amount_eth = estimate_buy_quantity(
        uniswap_v2,
        weth,
        usdc,
        usdc_amount_to_pay,
    )

    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1  # 1=success and mined

    # check if hot wallet get the same ETH amount estimated earlier
    assert weth.functions.balanceOf(hw_address).call() == pytest.approx(amount_eth)


def test_swap_with_slippage(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    user_2: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to buy WETH on Uniswap v2 using mock USDC."""

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
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction(
        {"from": deployer, "to": hw_address, "value": 1 * 10**18}
    )
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact(
        {"from": hw_address}
    )

    # give user_2 some cash as well
    usdc.functions.transfer(user_2, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact(
        {"from": user_2}
    )

    original_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        weth,
        1 * 10**18,
    )

    eth_amount_with_slippage = estimate_buy_quantity(
        uniswap_v2,
        weth,
        usdc,
        usdc_amount_to_pay,
        slippage=100,  # 100bps = 1%
    )

    # assert Decimal(price / 1e18) == pytest.approx(price2)
    path = [usdc.address, weth.address]

    # prepare a swap USDC->WETH
    tx1 = router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        eth_amount_with_slippage,
        path,
        hw_address,
        FOREVER_DEADLINE,
    ).buildTransaction({"from": hw_address})
    tx1 = fill_nonce(web3, tx1)
    signed_tx1 = hot_wallet.sign_transaction(tx1)

    # user_2 makes a faster trade which moves the price
    tx2 = router.functions.swapExactTokensForTokens(
        85 * 10**18,
        0,
        path,
        user_2,
        FOREVER_DEADLINE,
    ).transact({"from": user_2})

    # check the move percentage
    new_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        weth,
        1 * 10**18,
    )
    price_move_percent = original_price * 100 / new_price - 100
    assert 1 < price_move_percent < 1.1

    print(f"Price moved: {price_move_percent} %")

    # now the hotwallet finally manages to send the tx, it should fail
    tx1_hash = web3.eth.send_raw_transaction(signed_tx1.rawTransaction)
    tx1_receipt = web3.eth.get_transaction_receipt(tx1_hash)
    assert tx1_receipt.status == 0  # failure
