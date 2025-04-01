#!/bin/bash

set -e
set -u

export JSON_RPC_URL=$JSON_RPC_ETHEREUM
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_ARBITRUM
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_POLYGON
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_BASE
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_BERACHAIN
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_UNICHAIN
python scripts/erc-4626/scan-vaults.py

export JSON_RPC_URL=$JSON_RPC_AVALANCHE
python scripts/erc-4626/scan-vaults.py
