"""Ganache mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_ganache

"""
import logging
import os
import shutil

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.token import fetch_erc20_details

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("BNB_CHAIN_JSON_RPC") is None) or (shutil.which("anvil") is None),
    reason="Set BNB_CHAIN_JSON_RPC env install anvil command to run these tests",
)


@pytest.fixture()
def large_busd_holder() -> HexAddress:
    """A random account picked from BNB Smart chain that holds a lot of BUSD.

    This account is unlocked on Ganache, so you have access to good BUSD stash.

    `To find large holder accounts, use bscscan <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


@pytest.fixture()
def user_1() -> LocalAccount:
    """Create a test account."""
    return Account.create()


@pytest.fixture()
def user_2() -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.create()


@pytest.fixture()
def anvil_bnb_chain_fork(request, large_busd_holder, user_1, user_2) -> str:
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


@pytest.fixture()
def web3(anvil_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_bnb_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    return web3


def test_anvil_output():
    """Read anvil output from stdout."""
    # mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    # process, cmd = _launch("anvil")

    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    launch = fork_network_anvil(mainnet_rpc)
    stdout, stderr = launch.close()
    assert b"https://github.com/foundry-rs/foundry" in stdout, f"Did not see the market string in stdout: {stdout}"


def test_anvil_forked_chain_id(web3: Web3):
    """Anvil pipes through the forked chain id."""
    assert web3.eth.chain_id == 56


def test_anvil_fork_busd_details(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount):
    """Checks BUSD deployment on BNB chain."""
    busd = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    assert busd.symbol == "BUSD"
    assert (busd.total_supply / (10**18)) > 1_000_000_000, "More than $1B BUSD minted"


def test_anvil_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount):
    """Forks the BNB chain mainnet and transfers from USDC to the user."""

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Transfer 500 BUSD to the user 1
    tx_hash = busd.functions.transfer(user_1.address, 500 * 10**18).transact({"from": large_busd_holder})

    # Because Ganache has instamine turned on by default, we do not need to wait for the transaction
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1, "BUSD transfer reverted"

    assert busd.functions.balanceOf(user_1.address).call() == 500 * 10**18
