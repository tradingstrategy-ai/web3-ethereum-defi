"""Ganache mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.binance.org/"
    pytest -k test_ganache

"""

import os

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.provider.ganache import fork_network
from eth_defi.token import fetch_erc20_details

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
# pytestmark = pytest.mark.skipif(
#    os.environ.get("JSON_RPC_BINANCE") is None,
#    reason="Set JSON_RPC_BINANCE environment variable to Binance Smart Chain node to run this test",
# )

pytestmark = pytest.mark.skip(reason="Ganache is broken so horribly that do not even try to run tests as part of a normal suite")


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
def ganache_bnb_chain_fork(large_busd_holder, user_1, user_2) -> str:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_BINANCE"]
    launch = fork_network(mainnet_rpc, unlocked_addresses=[large_busd_holder])
    yield launch.json_rpc_url
    # Wind down Ganache process after the test is complete
    launch.close()


@pytest.fixture()
def web3(ganache_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(HTTPProvider(ganache_bnb_chain_fork))


# Because of Ganache
@flaky.flaky()
def test_mainnet_fork_busd_details(web3: Web3, large_busd_holder: HexAddress, user_1):
    """Checks BUSD deployment on BNB chain."""
    busd = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    assert busd.symbol == "BUSD"
    assert (busd.total_supply / (10**18)) > 10_000_000, "More than $10m BUSD minted"


# Because of Ganache
@flaky.flaky()
def test_mainnet_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1):
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
