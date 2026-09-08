"""Test MEV blocker provider switching."""

import datetime
import threading

import pytest
from web3 import HTTPProvider, Web3
from web3._utils.caching.caching_utils import generate_cache_key

from eth_defi.confirmation import (
    ConfirmationTimedOut,
    fetch_fresh_singleton_receipt,
    wait_and_broadcast_multiple_nodes_mev_blocker,
)
from eth_defi.provider.anvil import launch_anvil, AnvilLaunch
from eth_defi.provider.mev_blocker import MEVBlockerProvider, get_mev_blocker_provider
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.receipt import wait_for_transaction_receipt_robust

from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.abi import ZERO_ADDRESS
from eth_defi.compat import sessions
from eth_defi.tx import get_tx_broadcast_data


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def mev_blocker_provider(anvil: AnvilLaunch) -> MEVBlockerProvider:
    provider = MEVBlockerProvider(
        call_provider=HTTPProvider(anvil.json_rpc_url),
        transact_provider=HTTPProvider(anvil.json_rpc_url),
    )
    return provider


def test_mev_blocker_call(mev_blocker_provider: MEVBlockerProvider):
    """Read only methods route through the call provider"""
    web3 = Web3(mev_blocker_provider)
    block_number = web3.eth.block_number
    assert block_number == 0
    assert mev_blocker_provider.provider_counter["call"] == 1
    assert mev_blocker_provider.provider_counter["transact"] == 0


def test_mev_blocker_send_transaction(mev_blocker_provider: MEVBlockerProvider):
    """eth_sendTransaction goes through the MEV blocker"""
    web3 = Web3(mev_blocker_provider)
    account = web3.eth.accounts[0]
    assert mev_blocker_provider.provider_counter["call"] == 1
    assert mev_blocker_provider.provider_counter["transact"] == 0
    tx_hash = web3.eth.send_transaction({"to": ZERO_ADDRESS, "from": account, "value": 1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert mev_blocker_provider.provider_counter["call"] == 9
    assert mev_blocker_provider.provider_counter["transact"] == 1


def test_mev_blocker_send_transaction_raw(mev_blocker_provider: MEVBlockerProvider):
    """eth_sendTransactionRaw goes through the MEV blocker"""

    web3 = Web3(mev_blocker_provider)
    wallet = HotWallet.create_for_testing(web3)

    start_call_count = mev_blocker_provider.provider_counter["call"]

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    # Account for setup API counts from create_for_testing()
    assert mev_blocker_provider.provider_counter["call"] in (11, 12, 13)
    assert mev_blocker_provider.provider_counter["call"] == start_call_count + 1
    assert mev_blocker_provider.provider_counter["transact"] == 1

    raw_bytes = get_tx_broadcast_data(signed_tx)
    tx_hash = web3.eth.send_raw_transaction(raw_bytes)
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert mev_blocker_provider.provider_counter["call"] in (11, 12, 13)
    assert mev_blocker_provider.provider_counter["transact"] == 2


def test_mev_blocker_broadcast_single(mev_blocker_provider: MEVBlockerProvider):
    """Use wait_and_broadcast_multiple_nodes_mev_blocker() MEV Blocker compatible broadcasting method"""

    web3 = Web3(mev_blocker_provider)
    wallet = HotWallet.create_for_testing(web3)

    _mev_blocker = get_mev_blocker_provider(web3)
    assert _mev_blocker == mev_blocker_provider

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    receipts = wait_and_broadcast_multiple_nodes_mev_blocker(web3.provider, [signed_tx])

    assert len(receipts) == 1


def test_mev_blocker_broadcast_two(mev_blocker_provider: MEVBlockerProvider):
    """Use wait_and_broadcast_multiple_nodes_mev_blocker() MEV Blocker compatible broadcasting method"""

    web3 = Web3(mev_blocker_provider)
    wallet = HotWallet.create_for_testing(web3)

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    signed_tx_2 = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )
    receipts = wait_and_broadcast_multiple_nodes_mev_blocker(web3.provider, [signed_tx, signed_tx_2])

    assert len(receipts) == 2


def test_mev_blocker_broadcast_timeout(mev_blocker_provider: MEVBlockerProvider):
    """Use wait_and_broadcast_multiple_nodes_mev_blocker() MEV Blocker compatible broadcasting method"""

    web3 = Web3(mev_blocker_provider)
    wallet = HotWallet.create_for_testing(web3)

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    with pytest.raises(ConfirmationTimedOut):
        wait_and_broadcast_multiple_nodes_mev_blocker(web3.provider, [signed_tx], max_timeout=datetime.timedelta(seconds=-1))


def test_fresh_singleton_receipt_fetch_keeps_provider_instrumentation(anvil: AnvilLaunch) -> None:
    """Recover a transaction receipt without replacing the singleton provider.

    1. Broadcast a transaction through a singleton fallback provider.
    2. Probe its receipt using a fresh HTTP connection.
    3. Verify the receipt is returned through a new session without replacing the provider.
    """
    # 1. Broadcast a transaction through a singleton fallback provider.
    web3 = create_multi_provider_web3(anvil.json_rpc_url, retries=0)
    fallback_provider = web3.get_fallback_provider()
    wallet = HotWallet.create_for_testing(web3)
    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )
    tx_hash = web3.eth.send_raw_transaction(get_tx_broadcast_data(signed_tx))
    wait_for_transaction_receipt_robust(web3, tx_hash, confirmation_block_count=0)
    original_provider = fallback_provider.get_active_provider()
    cache_key = generate_cache_key(f"{threading.get_ident()}:{original_provider.endpoint_uri}")
    original_session = sessions.session_cache.get_cache_entry(cache_key)
    assert original_session is not None

    # 2. Fetch its receipt using a fresh HTTP connection.
    receipt = fetch_fresh_singleton_receipt(fallback_provider, tx_hash)

    # 3. Verify the receipt is returned through a new session without replacing the provider.
    assert receipt["transactionHash"] == tx_hash
    assert fallback_provider.get_active_provider() is original_provider
    assert sessions.session_cache.get_cache_entry(cache_key) is not original_session
    assert web3.eth.get_transaction_receipt(tx_hash)["transactionHash"] == tx_hash
