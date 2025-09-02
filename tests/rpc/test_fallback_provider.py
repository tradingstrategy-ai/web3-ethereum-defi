"""Test JSON-RPC provider fallback mechanism."""

import datetime
import os
from pprint import pformat
from unittest.mock import patch, DEFAULT

import pytest
import requests
from eth_account import Account

from eth_defi.confirmation import wait_and_broadcast_multiple_nodes, NonceMismatch
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.provider.broken_provider import get_default_block_tip_latency
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import launch_anvil, AnvilLaunch
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.abi import ZERO_ADDRESS
from eth_defi.tx import get_tx_broadcast_data
from eth_defi.compat import WEB3_PY_V7, clear_middleware, create_http_provider

CI = os.environ.get("CI") == "true"


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def provider_1(anvil):
    """Create HTTPProvider - middleware cleared separately for v6/v7 compatibility"""
    provider = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
    clear_middleware(provider)
    return provider


@pytest.fixture()
def provider_2(anvil):
    provider = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
    clear_middleware(provider)
    return provider


@pytest.fixture()
def fallback_provider(provider_1, provider_2) -> FallbackProvider:
    provider = FallbackProvider([provider_1, provider_2], sleep=0.1, backoff=1)
    return provider


@pytest.fixture()
def web3(fallback_provider) -> Web3:
    """Test account with built-in balance"""
    return Web3(fallback_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Test account with built-in balance"""
    return web3.eth.accounts[0]


def test_fallback_no_issue(anvil: AnvilLaunch, fallback_provider: FallbackProvider):
    """Callback goes through the first provider"""
    web3 = Web3(fallback_provider)
    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 0
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.currently_active_provider == 0
    assert fallback_provider.endpoint_uri == anvil.json_rpc_url
    web3.eth.block_number
    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 1
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.currently_active_provider == 0


def test_fallback_single_fault(fallback_provider: FallbackProvider, provider_1):
    """Fallback goes through the second provider when first fails"""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=requests.exceptions.ConnectionError):
        web3.eth.block_number

    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 0
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 1
    assert fallback_provider.currently_active_provider == 1


def test_fallback_double_fault(fallback_provider: FallbackProvider, provider_1, provider_2):
    """Fallback fails on both providers."""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=requests.exceptions.ConnectionError), patch.object(provider_2, "make_request", side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(requests.exceptions.ConnectionError):
            web3.eth.block_number

    assert fallback_provider.retry_count == 6


@pytest.mark.skip(reason="Web 6.12 breaks with MagicMock")
def test_fallback_double_fault_recovery(fallback_provider: FallbackProvider, provider_1, provider_2):
    """Fallback fails on both providers, but then recover."""

    web3 = Web3(fallback_provider)

    count = 0

    def borg_start(*args, **kwargs):
        nonlocal count
        count += 1
        if count <= 2:
            raise requests.exceptions.ConnectionError()
        return DEFAULT

    with patch.object(provider_1, "make_request", side_effect=borg_start), patch.object(provider_2, "make_request", side_effect=borg_start):
        web3.eth.block_number

    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 1
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.retry_count == 2
    assert fallback_provider.currently_active_provider == 0


def test_fallback_unhandled_exception(fallback_provider: FallbackProvider, provider_1):
    """Exception fallback provider cannot handle"""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            web3.eth.block_number


def test_fallback_nonce_too_low(web3, deployer: str):
    """Retry nonce too low errors with eth_sendRawTransaction,

    See if we can retry LlamanNodes nonce too low errors when sending multiple transactions.
    """

    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    user = Account.create()
    hot_wallet = HotWallet(user)

    tx1_hash = web3.eth.send_transaction({"from": deployer, "to": user.address, "value": 5 * 10**18})
    assert_transaction_success_with_explanation(web3, tx1_hash)

    hot_wallet.sync_nonce(web3)

    # First send a transaction with a correct nonce
    tx2 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx2)
    signed_tx2 = hot_wallet.sign_transaction_with_new_nonce(tx2)
    assert signed_tx2.nonce == 0
    raw_bytes = get_tx_broadcast_data(signed_tx2)
    tx2_hash = web3.eth.send_raw_transaction(raw_bytes)
    assert_transaction_success_with_explanation(web3, tx2_hash)

    fallback_provider = web3.provider
    assert fallback_provider.api_call_counts[0]["eth_sendRawTransaction"] == 1
    assert fallback_provider.api_retry_counts[0]["eth_sendRawTransaction"] == 0

    # Then send a transaction with too low nonce.
    # We are not interested that the transaction goes thru, only
    # that it is retried.
    tx3 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx3)
    hot_wallet.current_nonce = 0  # Spoof nonce
    signed_tx3 = hot_wallet.sign_transaction_with_new_nonce(tx3)
    assert signed_tx3.nonce == 0

    with pytest.raises(ValueError):
        # nonce too low happens during RPC call
        raw_bytes = get_tx_broadcast_data(signed_tx3)
        tx3_hash = web3.eth.send_raw_transaction(raw_bytes)
        web3.eth.wait_for_transaction_receipt(web3, tx3_hash)

    assert fallback_provider.api_retry_counts[0]["eth_sendRawTransaction"] == 3  # 5 attempts, 3 retries, the last retry does not count


@pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON") is None or CI,
    reason="Set JSON_RPC_POLYGON environment variable to a Polygon node, also does not seem to work on CI JSON-RPC",
)
def test_eth_call_not_having_block(fallback_provider: FallbackProvider, provider_1):
    """What happens if you ask data from non-existing block."""

    json_rpc_url = os.environ["JSON_RPC_POLYGON"]

    provider_urls = json_rpc_url.split(" ")
    if len(provider_urls) > 1:
        json_rpc_url = provider_urls[0]

    provider = HTTPProvider(json_rpc_url)
    # We don't do real fallbacks, but test the internal
    fallback_provider = FallbackProvider(
        [provider, provider],
        sleep=0.1,  # Low thresholds for unit test
        backoff=1,
        state_missing_switch_over_delay=0.1,
    )

    web3 = Web3(fallback_provider)

    # See that we have fallback provider latency configured
    assert get_default_block_tip_latency(web3) == 4

    usdc = fetch_erc20_details(web3, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC on Polygon

    bad_block = 1  # We get empty response if the contract has not been deployed yet

    try:
        with pytest.raises(ProbablyNodeHasNoBlock):
            usdc.contract.functions.balanceOf(ZERO_ADDRESS).call(block_identifier=bad_block)
    except Exception as e:
        # Happens on Github CI
        #  FAILED tests/rpc/test_fallback_provider.py::test_eth_call_not_having_block - eth_defi.provider.fallback.ExtraValueError: ***'code': -32000, 'message': 'state transitaion failed: inverted_index(v1-accounts.0-64.ef) at (0000000000000000000000000000000000000000, 5) returned value 0, but it out-of-bounds 100000000-5501010835. it may signal that .ef file is broke - can detect by `erigon seg integrity --check=InvertedIndex`, or re-download files'***
        headers = get_last_headers()
        raise RuntimeError(f"Error fetching balance at block {bad_block} with headers {pformat(headers)}") from e

    assert fallback_provider.api_retry_counts[0]["eth_call"] == 3  # 5 attempts, 3 retries, the last retry does not count


def test_broadcast_and_wait_multiple(web3: Web3, deployer: str):
    """Broadcast transactions through multiple nodes.

    In this case, we test by just having multiple fallback providers pointing to the same node.
    """

    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    user = Account.create()
    hot_wallet = HotWallet(user)

    # Fill in user wallet
    tx1_hash = web3.eth.send_transaction({"from": deployer, "to": user.address, "value": 5 * 10**18})
    assert_transaction_success_with_explanation(web3, tx1_hash)

    hot_wallet.sync_nonce(web3)

    # First send a transaction with a correct nonce
    tx2 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx2)
    signed_tx2 = hot_wallet.sign_transaction_with_new_nonce(tx2)

    tx3 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx3)
    signed_tx3 = hot_wallet.sign_transaction_with_new_nonce(tx3)

    # Use low timeouts so this should stress out the logic
    receipt_map = wait_and_broadcast_multiple_nodes(
        web3,
        [signed_tx2, signed_tx3],
        max_timeout=datetime.timedelta(seconds=10),
        node_switch_timeout=datetime.timedelta(seconds=1),
    )

    assert signed_tx2.hash in receipt_map
    assert signed_tx3.hash in receipt_map


def test_broadcast_and_wait_multiple_nonce_reuse(web3: Web3, deployer: str):
    """Detect nonce mismatch conditions.

    - Nonce reuse
    """

    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    user = Account.create()
    hot_wallet = HotWallet(user)

    # Fill in user wallet
    tx1_hash = web3.eth.send_transaction({"from": deployer, "to": user.address, "value": 5 * 10**18})
    assert_transaction_success_with_explanation(web3, tx1_hash)

    hot_wallet.sync_nonce(web3)

    # First send a transaction with a correct nonce
    tx2 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx2)
    signed_tx2 = hot_wallet.sign_transaction_with_new_nonce(tx2)

    # Use low timeouts so this should stress out the logic
    wait_and_broadcast_multiple_nodes(
        web3,
        [signed_tx2],
        max_timeout=datetime.timedelta(seconds=10),
        node_switch_timeout=datetime.timedelta(seconds=1),
        check_nonce_validity=True,
    )

    tx3 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx3)
    hot_wallet.current_nonce = 0  # Set to reused nonce
    signed_tx3 = hot_wallet.sign_transaction_with_new_nonce(tx3)

    with pytest.raises(NonceMismatch):
        wait_and_broadcast_multiple_nodes(
            web3,
            [signed_tx3],
            max_timeout=datetime.timedelta(seconds=10),
            node_switch_timeout=datetime.timedelta(seconds=1),
            check_nonce_validity=True,
        )


def test_broadcast_and_wait_multiple_nonce_too_high(web3: Web3, deployer: str):
    """Detect nonce mismatch conditions.

    - Nonce too high
    """

    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    user = Account.create()
    hot_wallet = HotWallet(user)

    # Fill in user wallet
    tx1_hash = web3.eth.send_transaction({"from": deployer, "to": user.address, "value": 5 * 10**18})
    assert_transaction_success_with_explanation(web3, tx1_hash)

    hot_wallet.sync_nonce(web3)
    hot_wallet.current_nonce = 999

    # First send a transaction with a correct nonce
    tx2 = {"chainId": web3.eth.chain_id, "from": user.address, "to": deployer, "value": 1 * 10**18, "gas": 30_000}
    HotWallet.fill_in_gas_price(web3, tx2)
    signed_tx2 = hot_wallet.sign_transaction_with_new_nonce(tx2)

    with pytest.raises(NonceMismatch):
        wait_and_broadcast_multiple_nodes(
            web3,
            [signed_tx2],
            max_timeout=datetime.timedelta(seconds=10),
            node_switch_timeout=datetime.timedelta(seconds=1),
            check_nonce_validity=True,
        )
