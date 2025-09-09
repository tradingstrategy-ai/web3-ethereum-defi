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

# Do 1h scan vs 1d scan
export FREQUENCY=1h
export MAX_WORKERS=50

SCAN_PRICES=${SCAN_PRICES:-"false"}

# Currently: disabled - HyperSync for Hyperliquid is stuck
export JSON_RPC_URL=$JSON_RPC_HYPERLIQUID
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

# Currently disabled: both dRPC and Alchemy broken for Optimism
export JSON_RPC_URL=$JSON_RPC_OPTIMISM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ABSTRACT
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ZKSYNC
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_GNOSIS
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ZORA
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_POLYGON
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_AVALANCHE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_BERACHAIN
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_UNICHAIN
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_SONIC
python scripts/erc-4626/scan-vaults.py
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

export JSON_RPC_URL=$JSON_RPC_BASE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_INK
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_BLAST
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_SONEIUM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_CELO
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

echo "Cleaning vault data"
python scripts/erc-4626/clean-prices.py


echo "All done"
