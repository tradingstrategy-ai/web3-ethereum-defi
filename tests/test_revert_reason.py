"""Revert reason testing.

Tests are performed using BNB Chain mainnet fork and Ganache.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_revert_reason

"""
import os
import random

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.txmonitor import wait_transactions_to_complete
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.ganache import fork_network
from eth_defi.token import fetch_erc20_details

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
from web3.middleware import construct_sign_and_send_raw_middleware

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

    We use 1 second block time, so that Ganache revert reason logic works correctly.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    launch = fork_network(mainnet_rpc, unlocked_addresses=[large_busd_holder], block_time=1)
    yield launch.json_rpc_url
    # Wind down Ganache process after the test is complete
    launch.close()


@pytest.fixture
def web3(user_1, ganache_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/latest/web3.eth.account.html#read-a-private-key-from-an-environment-variable
    web3 = Web3(HTTPProvider(ganache_bnb_chain_fork))
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(user_1))
    return web3


def test_revert_reason(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount, user_2: LocalAccount):
    """Revert reason can be extracted from the transaction.

    We test this by sending BUSD with insufficient token balance.
    """

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Make sure user_1 has enough BNB
    tx_hash = web3.eth.send_transaction({"from": large_busd_holder, "to": user_1.address, "value": 10**18})
    wait_transactions_to_complete(web3, [tx_hash])

    # user_1 doese not have BUSD so this tx will fail
    # and BUSD ERC-20 contract should give the revert reason
    tx_hash = busd.functions.transfer(user_2.address, 500 * 10**18).transact({"from": user_1.address, "gas": 500_000})

    receipts = wait_transactions_to_complete(web3, [tx_hash])

    # Check that the transaction reverted
    assert len(receipts) == 1
    receipt = receipts[tx_hash]
    assert receipt.status == 0

    reason = fetch_transaction_revert_reason(web3, tx_hash)
    assert reason == "VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance"
