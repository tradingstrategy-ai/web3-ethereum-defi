---
name: get-block-number
description: Get the latest block number for a blockchain using Web3.py and JSON-RPC environment variables
---

# Get latest block number

This skill retrieves the latest block number from a blockchain using the configured JSON-RPC environment variables and Web3.py.

ALWAYS USE SCRIPT. NEVER RELY ON THE HISTORICAL INFORMATION OR GUESS.

## Required inputs

1. **Chain name**: The blockchain to query (e.g., Ethereum, Arbitrum, Base, Polygon)

## Environment variables

The skill uses environment variables in the format `JSON_RPC_{CHAIN}` where `{CHAIN}` is the uppercase chain name:

- `JSON_RPC_ETHEREUM` - Ethereum mainnet
- `JSON_RPC_ARBITRUM` - Arbitrum One
- `JSON_RPC_BASE` - Base
- `JSON_RPC_POLYGON` - Polygon

You chan find these in `CHAIN_NAMES` and in `eth_defi.provider.env`

## Running the script

Generate and run a Python script to fetch the block number.
Run it Python commadn line inline, don't write a new file.

```python
import os
from web3 import Web3

from eth_defi.provider.multi_provider import create_multi_provider_web3

# Replace {CHAIN} with the uppercase chain name
json_rpc_url = os.environ.get("JSON_RPC_{CHAIN}")

if not json_rpc_url:
    raise ValueError("JSON_RPC_{CHAIN} environment variable not set")

web3 = create_multi_provider_web3(json_rpc_url)
block_number = web3.eth.block_number

print(f"Latest block number: {block_number}")
```

Run the script with:

```shell
source .local-test.env && poetry run python <script_path>
```

## Display output

Return the block number to the user in a clear format, e.g.:

```
Chain: Ethereum
Latest block number: 19,234,567
```
