[![PyPI version](https://badge.fury.io/py/web3-ethereum-defi.svg)](https://badge.fury.io/py/web3-ethereum-defi)

[![Automated test suite](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/badge.svg)](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml)

[![Documentation Status](https://readthedocs.org/projects/web3-ethereum-defi/badge/?version=latest)](https://web3-ethereum-defi.readthedocs.io/)

# Web3-Ethereum-Defi

Web-Ethereum-DeFi (`eth_defi`) allows you to integrate [EVM-compatible](https://tradingstrategy.ai/glossary/evm-compatible) Web3 and DeFi protocols into your Python application.

- [Use cases](#use-cases)
- [Supported protocols, chains and integrations](#supported-protocols-chains-and-integrations)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Example code](#example-code)
   * [Uniswap swap example](#uniswap-swap-example)
- [How to use the library in your Python project](#how-to-use-the-library-in-your-python-project)
- [Documentation](#documentation)
- [Development and contributing](#development-and-contributing)
- [Version history](#version-history)
- [Support ](#support)
- [Social media](#social-media)
- [License ](#license)

# Use cases

Use cases for this package include

- Trading and bots
- Data research, extraction, transformation and loading
- Portfolio management and accounting
- System integrations and backends
- AI agent interaction for EVM chains

# Supported protocols, chains and integrations

![Supported protocols include Uniswap, Aave, others](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/docs/source/_static/logo-wall-white.png)

| Protocol         | Actions                                                       | Tutorial and API links                                                                                                                                                          |
|:-----------------|:--------------------------------------------------------------|:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Uniswap          | Token swaps, data research                                    | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/make-uniswap-v3-swap-in-python.html)                                                                             |
| Gnosis Safe      | Safe deployment customisation and modules                     | [API](https://web3-ethereum-defi.readthedocs.io/api/safe/index.html)                                                                                                            |
| Circle USDC      | USDC interactions                                             | [API](https://web3-ethereum-defi.readthedocs.io/api/usdc/index.html)                                                                                                            |
| ChainLink        | Read oracle prices, set up oracles                            | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/chainlink-native-token.html)                                                                                     |
| CoW Swap         | Swaps, vault integration                                      | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-cowswap.html)                                                                                             |
| PancakeSwap      | Token swaps, data research                                    | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/pancakeswap-live-minimal.html)                                                                                   |
| GMX              | Leveraged trading, spot trading                               | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/gmx-swap.html)                                                                                                   |
| gTrade           | Leveraged trading, vaults                                     | [API](https://web3-ethereum-defi.readthedocs.io/api/gains/index.html)                                                                                                           |
| Ostium           | Leveraged trading, vaults                                     | [API](https://web3-ethereum-defi.readthedocs.io/api/gains/index.html)                                                                                                           |
| LFG              | Token swaps, data research                                    | [API](https://web3-ethereum-defi.readthedocs.io/api/uniswap_v2/index.html)                                                                                                      |
| Aave             | Credit, borrow, read rates                                    | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/aave-v3-interest-analysis.html)                                                                                  |
| BENQI            | Credit, borrow, read rates                                    | [API](https://web3-ethereum-defi.readthedocs.io/api/aave_v2/index.html)                                                                                                         |
| Lendle           | Credit, borrow, read rates                                    | [API](https://web3-ethereum-defi.readthedocs.io/api/aave_v2/index.html)                                                                                                         |
| Sky (MakerDAO)   | Credit, borrow, read rates                                    | [API](https://web3-ethereum-defi.readthedocs.io/api/aave_v3/index.html)                                                                                                         |
| Enzyme           | Deposit to vaults, deploy, read vault data                    | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/enzyme-read-vaults.html)                                                                                         |
| Lagoon           | Deposit to vaults, deploy, read vault data                    | [API](https://web3-ethereum-defi.readthedocs.io/api/lagoon/index.html)                                                                                                          |
| Velvet           | Deposit to vaults, deploy, read vault data                    | [API](https://web3-ethereum-defi.readthedocs.io/api/lagoon/index.html)                                                                                                          |           |
| Morpho           | Read vault data                                               | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/erc-4626-scan-prices.html)                                                                                       |
| Euler            | Read vault data                                               | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/erc-4626-scan-prices.html)                                                                                       |                                                                                                                                                                           |
| IPOR             | Read vault data                                               | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/erc-4626-scan-prices.html)                                                                                       |
| 1delta           | Open/close leveraged long/short positions                     | [API](https://web3-ethereum-defi.readthedocs.io/api/one_delta/index.html)                                                                                                       |
| Yearn            | Read vault data                                               | [API](https://web3-ethereum-defi.readthedocs.io/api/yearn/index.html)                                                                                                           |
| NashPoint        | Read vault data | [API](https://web3-ethereum-defi.readthedocs.io/api/nashpoint/index.html) |
| Untangle Finance | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/untangle/index.html) |
| Plutus           | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/plutus/index.html)|
| D2 Finance       | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/d2_finance/index.html)|
| Umami Finance    | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/untangle/index.html)|
| Harvest Finance  | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/harvest/index.html)|
| USDAi            | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/usdai/index.html)|
| AUTO Finance     | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/auto_finance/index.html)|
| Goat Protocol    | Read vault data|[API](https://web3-ethereum-defi.readthedocs.io/api/goat/index.html)|
| Hypersync        | Read historical data fast                                     | [API](https://web3-ethereum-defi.readthedocs.io/api/hypersync/index.html)                                                                                                       |
| Token Risk       | Glider Token Risk API by Hexens                               | [API](https://web3-ethereum-defi.readthedocs.io/api/token_analysis/_autosummary_token_analysis/eth_defi.token_analysis.tokenrisk.html#module-eth_defi.token_analysis.tokenrisk) |
| TokenSniffer     | Read token risk core and metricws                             | [API](https://web3-ethereum-defi.readthedocs.io/api/token_analysis/_autosummary_token_analysis/eth_defi.token_analysis.tokensniffer.html)                                       |
| Foundry          | Compile, deploy and verify smart contracts                    | [API](https://web3-ethereum-defi.readthedocs.io/api/foundry/_autosummary_forge/eth_defi.foundry.forge.html)                                                                     |
| Etherscan        | Deploy and verify smart contracts                             | [API](https://web3-ethereum-defi.readthedocs.io/api/etherscan/index.html)                                                                                                       |
| MEVBlocker       | Frontrun protection                                           | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/mev-blocker.html)                                                                                                |
| Ethereum mainnet | Frontrun protection, token mapping                            | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/mev-blocker.html)                                                                                                |                                                                                                                                                                                                                                                        |
| Base             | Frontrun protection, token mapping                            | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/mev-blocker.html)                                                                                                |
| Arbitrum         | Frontrun protection, token mapping                            | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/mev-blocker.html)                                                                                                |
| Avalanche        | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |                                                                                                                                                                        |
| BNB chain        | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Polygon          | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| BNB Chain        | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |                                                                                                                                                                           |
| Berachain        | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Avalanche        | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Hyperliquid      | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Mode             | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Unichain         | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| ZKSync           | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Soneium          | Token mapping                                                 | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html#module-eth_defi.token)                                                                |
| Google GCloud    | Support hardware security module wallets                      | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.gcloud_hsm_wallet.html)                                                                          |
| Hot wallet       | Secure hot wallet handling                                    | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.hotwallet.html)                                                                                  |
| Multicall3       | Chunked and historical data reading                           | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/multicall3-with-python.html)                                                                                     |
| Gas              | Ethereum gas management                                       | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.gas.html)                                                                                        |
| EIP-4626         | Vault analysis                                                | [Tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/erc-4626-best-vaults.html)                                                                                       |
| EIP-726          | Message signing and decoding                                  | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.eip_712.html#module-eth_defi.eip_712)                                                            |
| ERC-20           | High performance reading, data mappings                       | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.token.html)                                                                                      |
| ABI              | High performance smart contract ABI management                | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.abi.html)                                                                                        |
| Transactions     | Stack traces and symbolic revert reasons                      | [API](https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.gas.html)                                                                                        |
| Anvil            | Mainnet works and local unit testing                          | [API](https://web3-ethereum-defi.readthedocs.io/api/provider/_autosummary_provider/eth_defi.provider.anvil.html)                                                                |
| LlamaNodes       | Special RPC support                                           | [API](https://web3-ethereum-defi.readthedocs.io/api/provider/_autosummary_provider/eth_defi.provider.llamanodes.html#module-eth_defi.provider.llamanodes)                       |
| Ankr             | Special RPC support                                           | [API](https://web3-ethereum-defi.readthedocs.io/api/provider/_autosummary_provider/eth_defi.provider.ankr.html)                                                                 |
| dRPC             | Special RPC support                                           | [API](https://web3-ethereum-defi.readthedocs.io/api/event_reader/_autosummary_enzyne/eth_defi.event_reader.fast_json_rpc.get_last_headers.html?highlight=get_last_headers)      |


👉 [Read the full API documentation](https://web3-ethereum-defi.readthedocs.io/).

This is a MIT-licensed open source project. Those who sponsor and contribute get integrations.

# Prerequisites

To use this package you need to

* Have Python 3.10, Python 3.11, or Python 3.12 (no other versions tested)
* macOS, Linux or Windows Subsystem for Linux (WSL) needed, Microsoft Windows is not officially supported
  * For WSL, [make sure you have gcc and other tools installed](https://stackoverflow.com/questions/62215963/how-to-install-gcc-and-gdb-for-wslwindows-subsytem-for-linux/63548362#63548362)
* [Be proficient in Python programming](https://wiki.python.org/moin/BeginnersGuide)
* [Understand of Web3.py library](https://web3py.readthedocs.io/en/stable/) 
* [Understand Pytest basics](https://docs.pytest.org/)
 

# Install

With `pip`:

```shell
pip install "web3-ethereum-defi[data]"
```
**N.B.** From relase `0.32` this project will use `v7` of `web3py`. To keep using it with `v6` after the above setup run the following step as well.

```shell
pip install "web3-ethereum-defi[web3v6]"
```

With `poetry`:

<!-- This issue seems to be fixed now -->
<!-- [//]: # (**N.B.** Currently poetry version `1.8.5` works perfectly. Poetry `>= 2` will be stuck in an infinite loop ) -->

```shell
# Poetry version to use the latest web3py v7
poetry add -E data web3-ethereum-defi

# for web3py v6 
poetry add -E web3v6 web3-ethereum-defi
```

With `poetry` - master Git branch: 

```shell
git clone git@github.com:tradingstrategy-ai/web3-ethereum-defi.git
cd web3-ethereum-defi
poetry shell
poetry install --all-extras

# Additional step To force use web3py v6
poetry install -E web3v6
```

# Example code

See [the tutorials section in the documentation](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html)
for full code examples.

## Uniswap swap example

- This example shows how to make a trade on Uniswap v3.
- The example is for Polygon, but works on other chains.
- See [tutorials](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html) for more Uniswap and other DEX examples

```python

import datetime
import decimal
import os
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_defi.compat import construct_sign_and_send_raw_middleware

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.confirmation import wait_transactions_to_complete
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
print(f"Your have {gas_balance / (10 ** 18)} for gas fees")

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
    pool_fees=[500],   # 5 BPS pool WETH-USDC
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
print(f"After swap, you have {gas_balance / (10 ** 18)} native token left")
```

# How to use the library in your Python project

Add `web3-ethereum-defi` as a development dependency:

Using [Poetry](https://python-poetry.org/):

```shell
# Data optional dependencies include pandas and gql, needed to fetch Uniswap v3 data
poetry add -D "web3-ethereum-defi[data]"
```

# Documentation

- [Browse API documentation](https://web3-ethereum-defi.readthedocs.io/).
- [Browse tutorials](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html).

# Development and contributing

- [Read development instructions](https://web3-ethereum-defi.readthedocs.io/development.html).

# Version history

- [Read changelog](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/CHANGELOG.md).
- [See releases](https://pypi.org/project/web3-ethereum-defi/#history).

# Support 

- [Join Discord for any questions](https://tradingstrategy.ai/community).

# Social media

- [Watch tutorials on YouTube](https://www.youtube.com/@tradingstrategyprotocol)
- [Follow on Twitter](https://twitter.com/TradingProtocol)
- [Follow on Telegram](https://t.me/trading_protocol)
- [Follow on LinkedIn](https://www.linkedin.com/company/trading-strategy/)

# License 

MIT.

[Created by Trading Strategy](https://tradingstrategy.ai).
