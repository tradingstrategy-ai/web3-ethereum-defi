#!/bin/bash
#
# Scan vaults for a single
#
# - Read vault name from the input
# - scan-vaults.py will pick the correct chain id and HyperSync client server
# - set SCAN_PRICES=true is you want to scan also prices
# - Uses the same RPC config as scan-vaults-all-chains.sh
#
# To do a full scan for a single chain:
#
#
#
#    scripts/erc-4626/scan-single-chain.sh ethereum
#

set -e
set -u

echo "Scanning vault data for a single chain"

# Do 1h scan vs 1d scan
export FREQUENCY=1h

SCAN_PRICES=${SCAN_PRICES:-"true"}

MAX_WORKERS=${MAX_WORKERS:-"50"}
echo "Using $MAX_WORKERS workers"
export MAX_WORKERS

# Read first argument and convert to uppercase
CHAIN_NAME=$(echo "$1" | tr '[:lower:]' '[:upper:]')

# Construct the variable name
RPC_VAR_NAME="JSON_RPC_${CHAIN_NAME}"

echo "Chain name: $CHAIN_NAME"
echo "Using RPC variable: $RPC_VAR_NAME"

# Set JSON_RPC_URL using indirect variable expansion
export JSON_RPC_URL="${!RPC_VAR_NAME}"

python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then 
    SCAN_BACKEND=rpc python scripts/erc-4626/scan-prices.py ; 
fi

echo "Cleaning vault data"
python scripts/erc-4626/clean-prices.py

echo "Creating sparkline images"
python scripts/erc-4626/export-sparklines.py

echo "All done"
