"""A sample script to check that we get revert reason from BNB Chain JSON-RPC."""

import datetime
import logging
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import HTTPProvider, Web3
from web3.datastructures import AttributeDict

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.revert_reason import fetch_transaction_revert_reason

# Trace down to DEBUG level what the heck is going on
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


node = os.environ["JSON_RPC_BINANCE"]
private_key = os.environ["PRIVATE_KEY"]

web3 = Web3(HTTPProvider(node))

# We need
account: LocalAccount = Account.from_key(private_key)
web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

print(f"{node} current block is {web3.eth.block_number:,}")

busd_token = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"
received_address = "0xc6922E3e8CC8f32ffB17661fAE71eDCCac6A3c56"
amount = web3.toWei(Decimal("0.0000001"), "ether")

# Set legacy gas price strategy as BNB Chain is not London hardfork compatible
web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

# Attempt to send 1M BUSD that should revert
token = get_deployed_contract(web3, "ERC20MockDecimals.json", busd_token)
tx_hash = token.functions.transfer(
    received_address,
    1_000_0000 * 10**18,
).transact(
    {
        "from": account.address,
        "gas": 500_000,  # Gas must be set or we are going to get an exception in the gas estimate
    }
)

receipts = wait_transactions_to_complete(web3, [tx_hash], max_timeout=datetime.timedelta(minutes=1), confirmation_block_count=3)

# https://stackoverflow.com/a/39292086/315168
assert len(receipts) == 1
receipt: AttributeDict = next(iter(receipts.values()))

print(f"Transaction mined in block {receipt.blockNumber:,}, view it at https://bscscan.com/tx/{receipt.transactionHash.hex()}")
assert receipt.status == 0, "Did not fail?"

# Check the failure reason
reason = fetch_transaction_revert_reason(web3, tx_hash)
print(f"Got revert reason: {reason}")
