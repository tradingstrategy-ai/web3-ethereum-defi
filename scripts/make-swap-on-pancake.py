"""Make a swap on PancakeSwap Python example script.

This is an simple example script to swap one token to another securely.
It works on any `Uniswap v2 compatible DEX <https://tradingstrategy.ai/glossary/uniswap>`__.
For this particular example, we use PancakeSwap on Binance Smart Chain,
but you can reconfigure the script for any Uniswap v2 compatible protocol
on any `EVM-compatible <https://tradingstrategy.ai/glossary/evm-compatible>`__ blockchain.

- :ref:`Read tutorials section for required Python knowledge, version and how to install related packages <tutorials>`

- In order to run this example script, you need to have
  - Private key on BNB Smart Chain with BNB balance,
    `you can generate a private key on a command line using these instructions <https://ethereum.stackexchange.com/a/125699/620>`__.
  - `Binance Smart Chain JSON-RPC node <https://docs.bnbchain.org/docs/rpc>`. You can use public ones.
  - BUSD balance (you can swap some BNB on BUSD manually by importing your private key to a wallet)
  - Easy way to get few dollars worth of starting tokens is https://global.transak.com/
     with debit card - they support buying tokens natively for many blockchains.
  - Easy way to to manually swap is to import your private key to `Rabby desktop wallet <https://rabby.io/>`__.

This script will

- Sets up a private key with BNB gas money

- Sets up PancakeSwap instance

- Makes a swap from BUSD (base token) to Binance custodied ETH (quote token) for
  any amount of tokens you input

- `Uses slippage protection <https://tradingstrategy.ai/glossary/slippage>`__
  for the swap so that you do not get exploited by `MEV bots <https://tradingstrategy.ai/glossary/mev>`__

- Wait for the transaction to complete and display the reason if the trade succeeded or failed

To run:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.bnbchain.org"
    export PRIVATE_KEY="your private key here"
    python scripts/make-swap-on-pancake.py

"""

import datetime
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection

# The address of a token we are going to swap out
#
# Use https://tradingstrategy.ai/search to find your token
#
# For quote terminology see https://tradingstrategy.ai/glossary/quote-token
#
QUOTE_TOKEN_ADDRESS = "0xe9e7cea3dedca5984780bafc599bd69add087d56"  # BUSD

# The address of a token we are going to receive
#
# Use https://tradingstrategy.ai/search to find your token
#
# For base terminology see https://tradingstrategy.ai/glossary/base-token
BASE_TOKEN_ADDRESS = "0x2170ed0880ac9a755fd29b2688956bd959f933f8"  # Binance custodied ETH on BNB Chain

# Connect to JSON-RPC node
json_rpc_url = os.environ.get("JSON_RPC_BINANCE")
assert json_rpc_url, "You need to give JSON_RPC_BINANCE node URL. Check https://docs.bnbchain.org/docs/rpc for options"

web3 = Web3(HTTPProvider(json_rpc_url))

# Proof-of-authority middleware is needed to connect non-Ethereum mainnet chains
# (BNB Smart Chain, Polygon, etc...)
#
# Note that you might need to make a pull request to update
# POA_MIDDLEWARE_NEEDED_CHAIN_IDS for any new blockchain
# https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/ca29529b3b4306623273a40a85c9d155834cf249/eth_defi/chain.py#L25
#
install_chain_middleware(web3)

# Depending on a blockchain, it may or may not use EIP-1559
# based gas pricing and we may need to adjust gas price strategy
# for the outgoing transaction
web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

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

print(f"Uniwap v2 compatible router set to {dex.router.address}")

# Read and setup a local private key
private_key = os.environ.get("PRIVATE_KEY")
assert private_key is not None, "You must set PRIVATE_KEY environment variable"
assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"
account: LocalAccount = Account.from_key(private_key)
my_address = account.address

# Enable eth_sendTransaction using this private key
web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

# Read on-chain ERC-20 token data (name, symbol, etc.)
base = fetch_erc20_details(web3, BASE_TOKEN_ADDRESS)
quote = fetch_erc20_details(web3, QUOTE_TOKEN_ADDRESS)

# Native token balance
# See https://tradingstrategy.ai/glossary/native-token
gas_balance = web3.eth.get_balance(account.address)

print(f"Your address is {my_address}")
print(f"Your have {base.fetch_balance_of(my_address)} {base.symbol}")
print(f"Your have {quote.fetch_balance_of(my_address)} {quote.symbol}")
print(f"Your have {gas_balance / (10**18)} for gas fees")

assert quote.fetch_balance_of(my_address) > 0, f"Cannot perform swap, as you have zero {quote.symbol} needed to swap"

# Ask for transfer details
decimal_amount = input(f"How many {quote.symbol} tokens you wish to swap to {base.symbol}? ")

# Some input validation
try:
    decimal_amount = Decimal(decimal_amount)
except ValueError as e:
    raise AssertionError(f"Not a good decimal amount: {decimal_amount}") from e

# Fat-fingering check
print(f"Confirm swap amount {decimal_amount} {quote.symbol} to {base.symbol}")
confirm = input("Ok [y/n]?")
if not confirm.lower().startswith("y"):
    print("Aborted")
    sys.exit(1)

# Convert a human-readable number to fixed decimal with 18 decimal places
raw_amount = quote.convert_to_raw(decimal_amount)

# Each DEX trade is two transactions
# - ERC-20.approve()
# - swap (various functions)
# This is due to bad design of ERC-20 tokens,
# more here https://twitter.com/moo9000/status/1619319039230197760

# Uniswap router must be allowed to spent our quote token
approve = quote.contract.functions.approve(dex.router.address, raw_amount)

tx_1 = approve.build_transaction(
    {
        # approve() may take more than 500,000 gas on Arbitrum One
        "gas": 850_000,
        "from": my_address,
    }
)

# Build a swap transaction with slippage protection
#
# Slippage protection is very important, or you
# get instantly overrun by MEV bots with
# sandwitch attacks
#
# https://tradingstrategy.ai/glossary/mev
#
#
bound_solidity_func = swap_with_slippage_protection(
    dex,
    base_token=base,
    quote_token=quote,
    max_slippage=5,  # Allow 5 BPS slippage before tx reverts
    amount_in=raw_amount,
    recipient_address=my_address,
)

tx_2 = bound_solidity_func.build_transaction(
    {
        # Uniswap v2 swap should not take more than 1M gas units.
        # We do not use automatic gas estimation, as it is unreliable
        # and the number here is the maximum value only.
        # Only way to know this number is by trial and error
        # and experience.
        "gas": 1_000_000,
        "from": my_address,
    }
)

# Sign and broadcast the transaction using our private key
tx_hash_1 = web3.eth.send_transaction(tx_1)
tx_hash_2 = web3.eth.send_transaction(tx_2)

# This will raise an exception if we do not confirm within the timeout.
# If the timeout occurs the script abort and you need to
# manually check the transaction hash in a blockchain explorer
# whether the transaction completed or not.
tx_wait_minutes = 2.5
print(f"Broadcasted transactions {tx_hash_1.hex()}, {tx_hash_2.hex()}, now waiting {tx_wait_minutes} minutes for it to be included in a new block")
receipts = wait_transactions_to_complete(
    web3,
    [tx_hash_1, tx_hash_2],
    max_timeout=datetime.timedelta(minutes=tx_wait_minutes),
    confirmation_block_count=1,
)

# Check if any our transactions failed
# and display the reason
for completed_tx_hash, receipt in receipts.items():
    if receipt["status"] == 0:
        revert_reason = fetch_transaction_revert_reason(web3, completed_tx_hash)
        raise AssertionError(f"Our transaction {completed_tx_hash.hex()} failed because of: {revert_reason}")

print("All ok!")
print(f"After swap, you have {base.fetch_balance_of(my_address)} {base.symbol}")
print(f"After swap, you have {quote.fetch_balance_of(my_address)} {quote.symbol}")
print(f"After swap, you have {gas_balance / (10**18)} native token left")
