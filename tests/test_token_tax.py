"""Ganache mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_token_tax

"""
import os
import random

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import EthereumTesterProvider, HTTPProvider, Web3
from eth_defi.token_tax import estimate_token_taxes

from eth_defi.ganache import GanacheLaunch, fork_network
from eth_defi.utils import approx
from eth_defi.token_tax import TokenTaxInfo
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    os.environ.get("BNB_CHAIN_JSON_RPC") is None,
    reason="Set BNB_CHAIN_JSON_RPC environment variable to Binance Smart Chain node to run this test",
)


@pytest.fixture(scope="module")
def large_busd_holder() -> HexAddress:
    """A random account picked from BNB Smart chain that holds a lot of BUSD.

    This account is unlocked on Ganache, so you have access to good BUSD stash.

    `To find large holder accounts, use bscscan <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


@pytest.fixture(scope="module")
def user_1() -> LocalAccount:
    """Create a test account."""
    return Account.from_key(hex(random.randint(1, 2**256)))


@pytest.fixture(scope="module")
def user_2() -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.from_key(hex(random.randint(1, 2**256)))


@pytest.fixture(scope="module")
def ganache_bnb_chain_fork(large_busd_holder, user_1, user_2) -> str:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    launch = fork_network(mainnet_rpc, unlocked_addresses=[large_busd_holder])
    yield launch.json_rpc_url
    # Wind down Ganache process after the test is complete
    launch.close()

SUSHISWAP_FACTORYV2 = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"
PANCAKESWAP_FACTORYV2 = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
PANCAKE_ROUTER="0x10ED43C718714eb63d5aA57B78B54704E256024E"
PANCAKE_CODE_HASH="0x00fb7f630766e6a796048ea87d01acd3068e8ff67d078148a3fa3f4a84f69bd5"

ELEPHANT_TOKEN="0xE283D0e3B8c102BAdF5E8166B73E02D96d92F688"
BUSD_TOKEN="0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"

@pytest.fixture
def web3(ganache_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(HTTPProvider(ganache_bnb_chain_fork))

def test_token_tax(web3: Web3, large_busd_holder: HexAddress):
    
    uniswap: UniswapV2Deployment = fetch_deployment(web3, PANCAKESWAP_FACTORYV2, PANCAKE_ROUTER, PANCAKE_CODE_HASH)

    elephant = HexAddress(HexStr(ELEPHANT_TOKEN))
    busd = HexAddress(HexStr(BUSD_TOKEN))

    expected_elephant_tax_percent : float = 0.1

    seller = web3.eth.accounts[5]

    tokenTaxInfo: TokenTaxInfo = estimate_token_taxes(uniswap, elephant, busd, large_busd_holder, seller)

    # asserting if the elephant tax is close to 10% or not
    approx(tokenTaxInfo.buy_tax, expected_elephant_tax_percent, 0.001)
    approx(tokenTaxInfo.transfer_tax, expected_elephant_tax_percent, 0.001)
    approx(tokenTaxInfo.sell_tax, expected_elephant_tax_percent, 0.001)