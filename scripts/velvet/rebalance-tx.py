"""


Get vault data::

    https://api.velvet.capital/api/v3/portfolio/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25

"""

import os

import requests
import web3
from eth_account import Account

from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3

private_key = os.environ['PRIVATE_KEY']
json_rpc_base = os.environ['JSON_RPC_BASE']

api_url = "https://eventsapi.velvetdao.xyz/api/v3/rebalance/txn"

# owner: 0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f
# 0x59b9263c01e3bb1a888698da8a74afac67286d7f
payload = {
  "rebalanceAddress": "0x59b9263c01e3bb1a888698da8a74afac67286d7f",
  "sellToken": "0x6921b130d297cc43754afba22e5eac0fbf8db75b",
  "buyToken": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
  "sellAmount": "100000000000000000000",
  "slippage": "100",
  "remainingTokens": [
    "0x6921b130d297cc43754afba22e5eac0fbf8db75b",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
  ],
  "owner": "0x0C9dB006F1c7bfaA0716D70F012EC470587a8D4F"
}

resp = requests.post(api_url, json=payload)

data = resp.json()

print(data)

web3 = create_multi_provider_web3(json_rpc_base)

tx = {
    "to": data["to"],
    "data": data["data"],
    "gas": int(data["gasLimit"]),
    "gasPrice": int(data["gasPrice"]),
    "chainId": web3.eth.chain_id,
}

account = Account.from_key(private_key)

print("Our address is", account.address)
hot_wallet = HotWallet(account)
hot_wallet.sync_nonce(web3)

signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)

tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
print("Broadcasting", tx_hash.hex())

receipts = wait_transactions_to_complete(
    web3,
    [tx_hash],
)

print("TX receipt", receipts[tx_hash])

print("All good")

