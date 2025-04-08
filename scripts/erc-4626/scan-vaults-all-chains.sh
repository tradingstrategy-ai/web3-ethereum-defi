#!/bin/bash
#
# Scan vaults for all chains
#
# - Assume we have bunch of RPCs as JSON_RPC_ETHEREUM, JSON_RPC_xxx
# - scan-vaults.py will pick the correct chain id and HyperSync client server
# - set SCAN_PRICES=true is you want to scan also prices
#
# To do a full scan:
#
#     SCAN_PRICES=true scripts/erc-4626/scan-vaults-all-chains.sh
#

set -e
set -u

export JSON_RPC_URL=$JSON_RPC_BASE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_BERACHAIN
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_UNICHAIN
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ARBITRUM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ETHEREUM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_MODE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_BINANCE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_MANTLE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_HYPERLIQUID
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_POLYGON
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_AVALANCHE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

# Sonic net yet on HyperSync
# export JSON_RPC_URL=$JSON_RPC_SONIC
# python scripts/erc-4626/scan-vaults.py

