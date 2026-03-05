# How to download verified smart contract source code

## 1. Etherscan API (simplest)

```bash
# Get verified source code via API
curl "https://api.etherscan.io/api?module=contract&action=getsourcecode&address=0xCONTRACT_ADDRESS&apikey=YOUR_API_KEY"
```

The response JSON contains `SourceCode`, `ContractName`, `CompilerVersion`, and `ABI`. For multi-file contracts, `SourceCode` is a JSON object with all source files.

Works with all Etherscan-family explorers (Arbiscan, Basescan, Polygonscan, etc.) — just change the base URL.

## 2. Foundry's `forge` (best for full project structure)

```bash
# Download source + metadata into a directory
forge clone 0xCONTRACT_ADDRESS --etherscan-api-key YOUR_API_KEY

# For non-Ethereum chains
forge clone 0xCONTRACT_ADDRESS --chain base --etherscan-api-key YOUR_API_KEY
```

This reconstructs the full Foundry project structure with sources, remappings, and compiler settings. This is probably the best option for auditing.

## 3. Sourcify (no API key needed)

```bash
# Full match
curl "https://sourcify.dev/server/files/1/0xCONTRACT_ADDRESS"

# Partial match
curl "https://sourcify.dev/server/files/any/1/0xCONTRACT_ADDRESS"
```

Returns all source files if the contract is verified on Sourcify. The `1` is the chain ID.

## 4. Blockscout API

```bash
curl "https://eth.blockscout.com/api?module=contract&action=getsourcecode&address=0xCONTRACT_ADDRESS"
```

Same API format as Etherscan, no API key required for most Blockscout instances.

## 5. Python snippet

```python
import requests
import os

address = "0x..."
api_key = os.environ.get("ETHERSCAN_API_KEY", "")
chain_api = "https://api.etherscan.io"  # or api.basescan.org, api.arbiscan.io, etc.

resp = requests.get(f"{chain_api}/api", params={
    "module": "contract",
    "action": "getsourcecode",
    "address": address,
    "apikey": api_key,
})
data = resp.json()["result"][0]
source = data["SourceCode"]
# If multi-file, source starts with '{{' and is a JSON object
```

## Recommendation

For auditing purposes, `forge clone` is the recommended approach — it gives you a compilable project you can immediately run `slither`, `mythril`, or other analysis tools against.
