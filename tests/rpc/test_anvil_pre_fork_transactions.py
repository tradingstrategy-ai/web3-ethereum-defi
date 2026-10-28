"""Test reading transactions mined before the fork block through an Anvil fork."""

import os

import pytest
from web3.exceptions import TransactionNotFound

from eth_defi.provider.anvil import launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM needed")


def test_anvil_pre_fork_transaction_lookups():
    """Pre-fork transactions resolve only when the fork is launched with ``pre_fork_transaction_lookups``.

    The fork proxy answers transaction lookups locally with ``null`` by default,
    which keeps Anvil 1.8's per-transaction upstream lookups off slow providers.
    Tests replaying production state still need to read real transactions.

    1. Find a transaction in the fork block
    2. Check a default fork does not find it
    3. Check a fork launched with ``pre_fork_transaction_lookups`` finds its receipt
    """
    # 1. Find a transaction in the fork block
    # 2. Check a default fork does not find it
    default_fork = launch_anvil(JSON_RPC_ETHEREUM, fork_block_number=ETHEREUM_MIDNIGHT_BLOCK)
    try:
        web3 = create_multi_provider_web3(default_fork.json_rpc_url)
        tx_hash = web3.eth.get_block(ETHEREUM_MIDNIGHT_BLOCK)["transactions"][0]
        with pytest.raises(TransactionNotFound):
            web3.eth.get_transaction_receipt(tx_hash)
    finally:
        default_fork.close()

    # 3. Check a fork launched with pre_fork_transaction_lookups finds its receipt
    lookup_fork = launch_anvil(JSON_RPC_ETHEREUM, fork_block_number=ETHEREUM_MIDNIGHT_BLOCK, pre_fork_transaction_lookups=True)
    try:
        web3 = create_multi_provider_web3(lookup_fork.json_rpc_url)
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert receipt["blockNumber"] == ETHEREUM_MIDNIGHT_BLOCK
    finally:
        lookup_fork.close()
