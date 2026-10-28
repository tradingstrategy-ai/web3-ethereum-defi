"""Test that transaction sends to Anvil return only once the transaction is mined.

Anvil 1.8 automines asynchronously, so a plain send returns before the block
exists and every receipt wait sleeps a poll interval.
:py:class:`eth_defi.middleware.AnvilSyncSendMiddleware` sends through the
EIP-7966 sync methods instead.
"""

import time
from collections.abc import Iterator

import pytest
from eth_account import Account
from web3 import Web3

from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.hotwallet import HotWallet
from eth_defi.middleware import ANVIL_SYNC_SEND_TIMEOUT_MS, AnvilSyncSendMiddleware
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3


@pytest.fixture()
def anvil() -> Iterator[AnvilLaunch]:
    """Launch a local Anvil without a fork."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Connect to Anvil the way the code base does."""
    return create_multi_provider_web3(anvil.json_rpc_url)


def test_anvil_send_returns_mined_transaction(web3: Web3):
    """A send through ``create_multi_provider_web3()`` returns with the transaction already mined.

    Both send paths must be covered: unlocked accounts use ``eth_sendTransaction``,
    and hot wallets sign locally and use ``eth_sendRawTransaction``.

    1. Check the middleware is installed as the innermost layer
    2. Send from an unlocked account and read the receipt straight away
    3. Send a locally signed transaction and read the receipt straight away
    4. Check the confirmation helper returns without sleeping
    """
    # 1. Check the middleware is installed as the innermost layer
    middleware = [m for m, _ in web3.middleware_onion.middleware]
    assert middleware[-1] is AnvilSyncSendMiddleware

    # 2. Send from an unlocked account and read the receipt straight away
    sender, receiver = web3.eth.accounts[0], web3.eth.accounts[1]
    tx_hash = web3.eth.send_transaction({"from": sender, "to": receiver, "value": 1})
    assert web3.eth.get_transaction_receipt(tx_hash)["status"] == 1

    # 3. Send a locally signed transaction and read the receipt straight away
    account = Account.create()
    web3.eth.send_transaction({"from": sender, "to": account.address, "value": 10**18})
    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3)
    signed = hot_wallet.sign_transaction_with_new_nonce({"to": receiver, "value": 1, "gas": 21_000, "chainId": web3.eth.chain_id, "maxFeePerGas": 10**10, "maxPriorityFeePerGas": 1})
    raw_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert web3.eth.get_transaction_receipt(raw_hash)["status"] == 1

    # 4. Check the confirmation helper returns without sleeping
    started = time.perf_counter()
    receipts = wait_transactions_to_complete(web3, [tx_hash, raw_hash])
    assert len(receipts) == 2
    assert time.perf_counter() - started < 0.5


def test_anvil_send_without_automine_returns_hash(web3: Web3):
    """With automining off, both send paths still return the hash after the bounded wait.

    Tests that mine on demand must keep working: the raw send's sync call
    times out and returns the hash from the EIP-7966 timeout error, and the
    unlocked-account send stops polling for its receipt.

    1. Fund a hot wallet, then turn automining off
    2. Send from an unlocked account and check the hash comes back after the timeout, unmined
    3. Send a locally signed transaction and check the same
    4. Mine a block and check both transactions are in it
    """
    # 1. Fund a hot wallet, then turn automining off
    sender, receiver = web3.eth.accounts[0], web3.eth.accounts[1]
    account = Account.create()
    web3.eth.send_transaction({"from": sender, "to": account.address, "value": 10**18})
    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3)
    web3.provider.make_request("evm_setAutomine", [False])

    # 2. Send from an unlocked account and check the hash comes back after the timeout, unmined
    started = time.perf_counter()
    tx_hash = web3.eth.send_transaction({"from": sender, "to": receiver, "value": 1})
    assert time.perf_counter() - started >= ANVIL_SYNC_SEND_TIMEOUT_MS / 1000
    assert web3.eth.get_transaction(tx_hash)["blockNumber"] is None

    # 3. Send a locally signed transaction and check the same
    signed = hot_wallet.sign_transaction_with_new_nonce({"to": receiver, "value": 1, "gas": 21_000, "chainId": web3.eth.chain_id, "maxFeePerGas": 10**10, "maxPriorityFeePerGas": 1})
    started = time.perf_counter()
    raw_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert time.perf_counter() - started >= ANVIL_SYNC_SEND_TIMEOUT_MS / 1000
    assert web3.eth.get_transaction(raw_hash)["blockNumber"] is None

    # 4. Mine a block and check both transactions are in it
    web3.provider.make_request("evm_mine", [])
    assert web3.eth.get_transaction_receipt(tx_hash)["status"] == 1
    assert web3.eth.get_transaction_receipt(raw_hash)["status"] == 1
