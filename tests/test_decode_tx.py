"""Test transaction decoding.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_decode_tx

"""
import os
import logging
import shutil

import pytest

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.tx import decode_signed_transaction


# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("BNB_CHAIN_JSON_RPC") is None) or (shutil.which("anvil") is None),
    reason="Set BNB_CHAIN_JSON_RPC env install anvil command to run these tests",
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
    return Account.create()


@pytest.fixture()
def anvil_bnb_chain_fork(request, large_busd_holder) -> str:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_busd_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = create_multi_provider_web3(anvil_bnb_chain_fork)
    return web3


@pytest.fixture
def hot_wallet(user_1, web3: Web3) -> HotWallet:
    """Hot wallet implementation."""
    assert isinstance(user_1, LocalAccount)
    wallet = HotWallet(user_1)
    wallet.sync_nonce(web3)
    return wallet


def test_bnb_chain_decode_tx(web3: Web3, large_busd_holder: HexAddress, hot_wallet: HotWallet):
    """Decoding transactions targeting BNB chain."""

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Create a spoofed transfer() (never executed)
    raw_tx = busd.functions.transfer("0x0000000000000000000000000000000000000000", 500 * 10**18).build_transaction({"gas": 100_000})
    signed_tx = hot_wallet.sign_transaction_with_new_nonce(raw_tx)
    signed_tx_bytes = signed_tx.rawTransaction
    d = decode_signed_transaction(signed_tx_bytes)
    assert d["nonce"] == 0
    assert d["data"].hex().startswith("0xa9059cbb0")  # transfer() function selector
