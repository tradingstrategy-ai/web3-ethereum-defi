"""Uniswap v2 synthetic data generation tests."""
import secrets
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3

from eth_defi.token import create_token, TokenDetails, fetch_erc20_details, reset_default_token_cache
from eth_defi.uniswap_v2.deployment import (
    UniswapV2Deployment,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.synthetic_data import generate_fake_uniswap_v2_data


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

    # This test does not work with token cache
    reset_default_token_cache()

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
def usdc(web3, deployer) -> TokenDetails:
    """Mock USDC token."""
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6, decimals=6)
    return fetch_erc20_details(web3, token.address)


@pytest.fixture()
def weth(uniswap_v2) -> TokenDetails:
    """Mock WETH token."""
    return fetch_erc20_details(uniswap_v2.web3, uniswap_v2.weth.address)


def test_generate_uniswap_v2_synthetic_data(uniswap_v2, deployer, weth, usdc):
    """Generate random ETH-USD trades over 5 minutes."""

    stats = generate_fake_uniswap_v2_data(
        uniswap_v2,
        deployer,
        weth,
        usdc,
        base_liquidity=100 * 10**18,  # 100 ETH liquidity
        quote_liquidity=1600 * 100 * 10**6,  # 170,000 USDC liquidity,
        number_of_blocks=3,
    )

    assert stats["initial_price"] == pytest.approx(Decimal("1600"))
    assert stats["buys"] == 1
    assert stats["sells"] == 2
    assert stats["min_price"] > 1400
    assert stats["max_price"] < 1700

    # Do 1 block worth of more trades
    round_two = generate_fake_uniswap_v2_data(
        uniswap_v2,
        deployer,
        weth,
        usdc,
        pair_address=stats["pair_address"],
        number_of_blocks=1,
    )

    assert round_two["initial_price"] == pytest.approx(Decimal("1593.422429595317817472610032"))
    assert round_two["buys"] == 0
    assert round_two["sells"] == 1
