"""Test HyperEVM dual-block architecture helpers.

Tests for :py:mod:`eth_defi.hyperliquid.block` — checking and toggling
the large block mode on HyperEVM.

Requires ``HYPERCORE_WRITER_TEST_PRIVATE_KEY`` environment variable
with a funded HyperEVM testnet account.
"""

import os

import pytest
from eth_account import Account

from eth_defi.hyperliquid.block import (
    HYPEREVM_CHAIN_IDS,
    fetch_using_big_blocks,
    is_hyperevm,
    set_big_blocks,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3

HYPERCORE_WRITER_TEST_PRIVATE_KEY = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY")

#: Default public RPC for HyperEVM testnet
HYPERLIQUID_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"

pytestmark = pytest.mark.skipif(
    not HYPERCORE_WRITER_TEST_PRIVATE_KEY,
    reason="HYPERCORE_WRITER_TEST_PRIVATE_KEY environment variable required",
)


@pytest.fixture()
def web3():
    """Connect to HyperEVM testnet."""
    return create_multi_provider_web3(HYPERLIQUID_TESTNET_RPC, default_http_timeout=(3, 30.0))


@pytest.fixture()
def deployer_address():
    """Get the deployer address from the private key."""
    account = Account.from_key(HYPERCORE_WRITER_TEST_PRIVATE_KEY)
    return account.address


def test_is_hyperevm():
    """Test HyperEVM chain ID detection."""
    assert is_hyperevm(998)
    assert is_hyperevm(999)
    assert not is_hyperevm(1)
    assert not is_hyperevm(42161)
    assert HYPEREVM_CHAIN_IDS == {998, 999}


def test_fetch_using_big_blocks(web3, deployer_address):
    """Read the current big block status from HyperEVM testnet."""
    result = fetch_using_big_blocks(web3, deployer_address)
    assert isinstance(result, bool)


def test_set_big_blocks_on_and_off(web3, deployer_address):
    """Toggle big blocks on and off on HyperEVM testnet.

    Verifies the full round-trip: enable, check, disable, check.
    """
    # Enable big blocks
    response = set_big_blocks(
        HYPERCORE_WRITER_TEST_PRIVATE_KEY,
        enable=True,
        is_mainnet=False,
    )
    assert response.get("status") == "ok", f"Unexpected response: {response}"

    # Verify enabled
    assert fetch_using_big_blocks(web3, deployer_address) is True

    # Disable big blocks
    response = set_big_blocks(
        HYPERCORE_WRITER_TEST_PRIVATE_KEY,
        enable=False,
        is_mainnet=False,
    )
    assert response.get("status") == "ok", f"Unexpected response: {response}"

    # Verify disabled
    assert fetch_using_big_blocks(web3, deployer_address) is False
