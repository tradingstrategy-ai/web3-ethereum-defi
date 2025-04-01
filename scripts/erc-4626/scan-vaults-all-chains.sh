#!/bin/bash
#
# Scan vaults for all chains
#
# - Assume we have bunch of RPCs as JSON_RPC_ETHEREUM, JSON_RPC_xxx
# - scan-vaults.py will pick the correct chain id and HyperSync client server
#

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
