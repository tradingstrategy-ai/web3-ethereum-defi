"""A sample script to check that our manually built transactions work against BNB Chain node.

Builds an offline transaction to send 0.000001 BUSD token and then tests this by broadcasting the tx.

Full traceback of the issue::

    /Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/eth.py:633: UserWarning: There was an issue with the method eth_maxPriorityFeePerGas. Calculating using eth_feeHistory.
      warnings.warn(
    Traceback (most recent call last):
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/eth.py", line 631, in max_priority_fee
        return self._max_priority_fee()
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/module.py", line 57, in caller
        result = w3.manager.request_blocking(method_str,
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/manager.py", line 198, in request_blocking
        return self.formatted_response(response,
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/manager.py", line 171, in formatted_response
        raise ValueError(response["error"])
    ValueError: {'code': -32601, 'message': 'the method eth_maxPriorityFeePerGas does not exist/is not available'}

    During handling of the above exception, another exception occurred:

    Traceback (most recent call last):
      File "/Users/moo/code/ts/eth-hentai/tests/manual_bnb_chain_build_transaction.py", line 34, in <module>
        ).buildTransaction({
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/contract.py", line 1079, in buildTransaction
        return build_transaction_for_function(
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/contract.py", line 1648, in build_transaction_for_function
        prepared_transaction = fill_transaction_defaults(web3, prepared_transaction)
      File "cytoolz/functoolz.pyx", line 250, in cytoolz.functoolz.curry.__call__
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/_utils/transactions.py", line 114, in fill_transaction_defaults
        default_val = default_getter(web3, transaction)
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/_utils/transactions.py", line 64, in <lambda>
        web3.eth.max_priority_fee + (2 * web3.eth.get_block('latest')['baseFeePerGas'])
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/eth.py", line 637, in max_priority_fee
        return fee_history_priority_fee(self)
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/_utils/fee_utils.py", line 45, in fee_history_priority_fee
        fee_history = eth.fee_history(*PRIORITY_FEE_HISTORY_PARAMS)  # type: ignore
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/eth.py", line 863, in fee_history
        return self._fee_history(block_count, newest_block, reward_percentiles)
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/module.py", line 57, in caller
        result = w3.manager.request_blocking(method_str,
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/manager.py", line 198, in request_blocking
        return self.formatted_response(response,
      File "/Users/moo/Library/Caches/pypoetry/virtualenvs/eth-hentai-kAuIg3tj-py3.10/lib/python3.10/site-packages/web3/manager.py", line 171, in formatted_response
        raise ValueError(response["error"])
    ValueError: {'code': -32601, 'message': 'the method eth_feeHistory does not exist/is not available'}

"""
import os
from decimal import Decimal

from web3 import Web3, HTTPProvider
from web3.datastructures import AttributeDict
from web3.gas_strategies.rpc import rpc_gas_price_strategy

from eth_defi.abi import get_deployed_contract
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.txmonitor import broadcast_and_wait_transactions_to_complete

node = os.environ["JSON_RPC_BINANCE"]
private_key = os.environ["PRIVATE_KEY"]

web3 = Web3(HTTPProvider(node))

print(f"{node} current block is {web3.eth.block_number:,}")

busd_token = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"
received_address = "0xc6922E3e8CC8f32ffB17661fAE71eDCCac6A3c56"
amount = web3.toWei(Decimal("0.0000001"), "ether")

wallet = HotWallet.from_private_key(private_key)
wallet.sync_nonce(web3)

print(f"Our hot wallet balance is: {wallet.get_native_currency_balance(web3)}")

default_gas_price_strategy = rpc_gas_price_strategy(web3)
print(f"Default gas price given by the node is: {default_gas_price_strategy}")

# Set legacy gas price strategy
web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

token = get_deployed_contract(web3, "ERC20MockDecimals.json", busd_token)
tx = token.functions.transfer(received_address, amount,).buildTransaction(
    {
        "chainId": web3.eth.chain_id,
        "gas": 100_000,  # Estimate max 100k per approval
        "from": wallet.address,
    }
)

signed = wallet.sign_transaction_with_new_nonce(tx)

print(f"Broadcasting tx {signed.hash.hex()}")
receipts = broadcast_and_wait_transactions_to_complete(web3, [signed])

# https://stackoverflow.com/a/39292086/315168
assert len(receipts) == 1
receipt: AttributeDict = next(iter(receipts.values()))

print(f"Transaction mined in block {receipt.blockNumber:,}, view it at https://bscscan.com/tx/{receipt.transactionHash.hex()}")

print("All ok!")
