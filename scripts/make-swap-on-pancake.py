"""Make a swap on PancakeSwap v2 Python example.

- You need to have
   - Private key on BNB Smart Chain with BNB balance
   - BUSD balance

This script will

- Sets up a private key with BNB gas money

- Sets up PancakeSwap instance

- Makes a swap from BUSD to Binance custodied ETH
  `using slippage protection <https://tradingstrategy.ai/glossary/slippage>`__

"""

import datetime
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import HTTPProvider, Web3
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.abi import get_deployed_contract
from eth_defi.token import fetch_erc20_details
from eth_defi.txmonitor import wait_transactions_to_complete

from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection

# What is the token we are transferring.
# Replace with your own token address.
ERC_20_TOKEN_ADDRESS = "0x0aC7B3733cBeE5D87A80fbf331f4A0bD01f17386"

# The address of a token we are going to swap out
#
# Use https://tradingstrategy.ai/search to find your token
#
# For quote terminology see https://tradingstrategy.ai/glossary/quote-token
#
QUOTE_TOKEN_ADDRESS="0xe9e7cea3dedca5984780bafc599bd69add087d56"  # BUSD

# The address of a token we are going to receive
#
# Use https://tradingstrategy.ai/search to find your token
#
# For base terminology see https://tradingstrategy.ai/glossary/base-token
BASE_TOKEN_ADDRESS="0x2170ed0880ac9a755fd29b2688956bd959f933f8"  # Binance custodied ETH on BNB Chain

# Connect to JSON-RPC node
json_rpc_url = os.environ.get("JSON_RPC_BINANCE")
assert json_rpc_url, "You need to give JSON_RPC_BINANCE node URL. Check https://docs.bnbchain.org/docs/rpc for options"

web3 = Web3(HTTPProvider(json_rpc_url))
print(f"Connected to blockchain, chain id is {web3.eth.chain_id}. the latest block is {web3.eth.block_number:,}")

# PancakeSwap data - all smart contract addresses
# and hashes we need to know from off-chain sources.
# Consult your DEX documentation for addresses.
dex = fetch_deployment(
    web3,
    factory_address="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
    router_address="0x10ED43C718714eb63d5aA57B78B54704E256024E",
    init_code_hash="0x00fb7f630766e6a796048ea87d01acd3068e8ff67d078148a3fa3f4a84f69bd5",
)

print(f"Uniwap v2 compatible router set to {dex.router.address")

# Read and setup a local private key
private_key = os.environ.get("PRIVATE_KEY")
assert private_key is not None, "You must set PRIVATE_KEY environment variable"
assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"
account: LocalAccount = Account.from_key(private_key)
my_address = account.address

# Enable eth_sendTransaction using this private key
web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

# Read on-chain token data
base = fetch_erc20_details(web3, BASE_TOKEN_ADDRESS)
quote = fetch_erc20_details(web3, QUOTE_TOKEN_ADDRESS)

# Native token balance
# See https://tradingstrategy.ai/glossary/native-token
gas_balance = web3.eth.getBalance(account.address)


print(f"Your have {base.fetch_balance_of(my_address)} {base.symbol}")
print(f"Your have {quote.fetch_balance_of(my_address)} {quote.symbol}")
print(f"Your have {gas_balance / (10 ** 18)} for gas fees")

# Ask for transfer details
decimal_amount = input(f"How many {quote.symbol} tokens you wish to swap to {base.symbol}? ")

# Some input validation
try:
    decimal_amount = Decimal(decimal_amount)
except ValueError as e:
    raise AssertionError(f"Not a good decimal amount: {decimal_amount}") from e

assert web3.is_checksum_address(to_address), f"Not a valid address: {to_address}"

# Fat-fingering check
print(f"Confirm swap amount {decimal_amount} {quote.symbol} to {base.symbol}")
confirm = input("Ok [y/n]?")
if not confirm.lower().startswith("y"):
    print("Aborted")
    sys.exit(1)

# Convert a human-readable number to fixed decimal with 18 decimal places
raw_amount = quote.convert_to_raw(decimal_amount)

# Build a swap transaction with slippage protection
#
# Slippage protection is very important, or you
# get instantly overrun by MEV bots with
# sandwitch attacks
#
# https://tradingstrategy.ai/glossary/mev
#
#
bound_solidity_tx = swap_with_slippage_protection(
    dex,
    base_token=base,
    quote_token=quote,
    max_slippage=5,  # Allow 5 BPS slippage before tx reverts
    amount_in=raw_amount,
)

tx = bound_solidity_tx.build_transaction({
    "gas": 1_000_000  # Uniswap v2 swap should not take more than 1M gas units
})

# Sign and broadcast the transaction using our private key
tx_hash = web3.eth.send_transaction(tx)

# This will raise an exception if we do not confirm within the timeout
print(f"Broadcasted transaction {tx_hash.hex()}, now waiting 5 minutes for mining")
wait_transactions_to_complete(web3, [tx_hash], max_timeout=datetime.timedelta(minutes=5))

print("All ok!")
print(f"After swap, you have {base.fetch_balance_of(my_address)} {base.symbol}")
print(f"After swap, you have {quote.fetch_balance_of(my_address)} {quote.symbol}")
print(f"After swap, you have {gas_balance / (10 ** 18)} native token left")
