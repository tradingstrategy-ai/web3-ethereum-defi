"""Broadcast a transation using Base sequencer directly.

- Manual test case, because we need to eventually test with real Base

To run:

.. code-block:: shell

    PRIVATE_KEY=... python scripts/base/base_sequencer.py
"""
import os
import logging
import sys
from decimal import Decimal
from pprint import pformat

from web3 import Web3

from eth_defi.confirmation import wait_and_broadcast_multiple_nodes_mev_blocker
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3


logging.basicConfig(level=logging.INFO, stream=sys.stdout)


def main():
    private_key = os.environ["PRIVATE_KEY"]
    # Configure direct-to-sequencer broadcast,
    # use public Base node for reads
    rpc_configuration_line = "mev+https://mainnet-sequencer.base.org https://mainnet.base.org"
    web3 = create_multi_provider_web3(rpc_configuration_line)

    assert web3.eth.chain_id == 8453  # Base

    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    # As a test transaction, send very small amount of ETH
    tx_data = {
        "chainId": web3.eth.chain_id,
        "from": hot_wallet.address,
        "to": "0x7612A94AafF7a552C373e3124654C1539a4486A8",  # Random addy
        "value": Web3.to_wei(Decimal("0.000001"), "ether"),
        "gas": 50_000,
    }

    hot_wallet.fill_in_gas_price(web3, tx_data)
    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx_data)

    # Blocks until included in a block
    print("Broadcasting", signed_tx.hash.hex())
    receipts = wait_and_broadcast_multiple_nodes_mev_blocker(
        web3.provider,
        txs=[signed_tx],
    )

    receipt = receipts[signed_tx.hash]
    print(f"Transaction broadcasted:\n{pformat(dict(receipt.items()))}")

if __name__ == '__main__':
    main()