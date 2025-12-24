"""An example script to make a token swap in Uniswap v3 using Python.

This is an simple example script to swap one token to another.
It works on any `Uniswap v3 compatible DEX <https://tradingstrategy.ai/glossary/uniswap>`__.
For this particular example, we use Uniswap v3 on Polygon,
but you can reconfigure the script for any  `EVM-compatible <https://tradingstrategy.ai/glossary/evm-compatible>`__
blockchain.

- :ref:`Read tutorials section for required Python knowledge, version and how to install related packages <tutorials>`

How to use

- Create a private key. `You can generate a private key on a command line using these instructions <https://ethereum.stackexchange.com/a/125699/620>`__.
  Store this private key safely e.g. in your password manager.

- Import the private key into a cryptocurrency wallet. We recommend `Rabby <https://rabby.io/>`__.

- Get MATIC (for gas gees) and USDC (for the trade) into the wallet.
  Note that Polygon has two different USDC flavours, native (USDC) and bridged (USDC.e).
  We use native USDC in this script. The easiest way is to buy MATIC in a centralised
  exchange and swap a bit it to USDC in Rabby internal swap function or uniswap.org.

- Configure environment variables and run this script

- The script will make you a swap, swapping 1 USDC for WETH on Uniswap v3

To run:

.. code-block:: shell

    export JSON_RPC_POLYGON="https://polygon-rpc.com"
    export PRIVATE_KEY="your private key here"
    python scripts/make-swap-on-uniwap-v3.py

"""

import datetime
import decimal
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection

# The address of a token we are going to swap out
#
# Use https://tradingstrategy.ai/search to find your token
#
# For quote terminology see https://tradingstrategy.ai/glossary/quote-token
#
QUOTE_TOKEN_ADDRESS = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"  # USDC (native)

# The address of a token we are going to receive
#
# Use https://tradingstrategy.ai/search to find your token
#
# For base terminology see https://tradingstrategy.ai/glossary/base-token
BASE_TOKEN_ADDRESS = "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"  # WETH


# Connect to JSON-RPC node
rpc_env_var_name = "JSON_RPC_POLYGON"
json_rpc_url = os.environ.get(rpc_env_var_name)
assert json_rpc_url, f"You need to give {rpc_env_var_name} node URL. Check ethereumnodes.com for options"

# Create a Web3 provider with ability to retry failed requests
# and supporting fallback JSON-RPC nodes. RPC connections
# are extremely flaky and for production grade usage you need to use multiple
# JSON-RPC nodes.
# create_multi_provider_web3() will also take care of any chain-specific
# RPC setup.
web3 = create_multi_provider_web3(json_rpc_url)

print(f"Connected to blockchain, chain id is {web3.eth.chain_id}. the latest block is {web3.eth.block_number:,}")

# Grab Uniswap v3 smart contract addreses for Polygon.
#
deployment_data = UNISWAP_V3_DEPLOYMENTS["polygon"]
uniswap_v3 = fetch_deployment(
    web3,
    factory_address=deployment_data["factory"],
    router_address=deployment_data["router"],
    position_manager_address=deployment_data["position_manager"],
    quoter_address=deployment_data["quoter"],
)

print(f"Using Uniwap v3 compatible router at {uniswap_v3.swap_router.address}")

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
except (ValueError, decimal.InvalidOperation) as e:
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
# and we do this by calling ERC20.approve() from our account
# to the token contract.
approve = quote.contract.functions.approve(uniswap_v3.swap_router.address, raw_amount)
tx_1 = approve.build_transaction(
    {
        # approve() may take more than 500,000 gas on Arbitrum One
        "gas": 850_000,
        "from": my_address,
    }
)

#
# Uniswap v3 may have multiple pools per
# trading pair differetiated by the fee tier. For example
# WETH-USDC has pools of 0.05%, 0.30% and 1%
# fees. Check for different options
# in https://tradingstrategy.ai/search
#
# Here we use 5 BPS fee pool (5/10,000).
#
#
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
    uniswap_v3,
    base_token=base,
    quote_token=quote,
    max_slippage=20,  # Allow 20 BPS slippage before tx reverts
    amount_in=raw_amount,
    recipient_address=my_address,
    pool_fees=[500],  # 5 BPS pool WETH-USDC
)

tx_2 = bound_solidity_func.build_transaction(
    {
        # Uniswap swap should not take more than 1M gas units.
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
print(f"View your transactions confirming at https://polygonscan/address/{my_address}")
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
