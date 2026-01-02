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

SCAN_PRICES=${SCAN_PRICES:-"false"}

MAX_WORKERS=${MAX_WORKERS:-"50"}
echo "Using $MAX_WORKERS workers"
export MAX_WORKERS

# export JSON_RPC_URL=$JSON_RPC_ABSTRACT
# python scripts/erc-4626/scan-vaults.py
# if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

# No quality RPC available
# export JSON_RPC_URL=$JSON_RPC_TAC
# python scripts/erc-4626/scan-vaults.py
# if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

# export JSON_RPC_URL=$JSON_RPC_ZKSYNC
# python scripts/erc-4626/scan-vaults.py
# if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

# Hypersync timing out
#   File "/usr/src/web3-ethereum-defi/eth_defi/erc_4626/hypersync_discovery.py", line 173, in scan_potential_vaults
#    res = await asyncio.wait_for(receiver.recv(), timeout=self.recv_timeout)
#export JSON_RPC_URL=$JSON_RPC_MODE
# python scripts/erc-4626/scan-vaults.py
# if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi


export JSON_RPC_URL=$JSON_RPC_MONAD
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_HYPERLIQUID
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_BASE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ARBITRUM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_ETHEREUM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi


export JSON_RPC_URL=$JSON_RPC_LINEA
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

export JSON_RPC_URL=$JSON_RPC_HEMI
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_PLASMA
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi


export JSON_RPC_URL=$JSON_RPC_BINANCE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_MANTLE
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then python scripts/erc-4626/scan-prices.py ; fi

export JSON_RPC_URL=$JSON_RPC_KATANA
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

export JSON_RPC_URL=$JSON_RPC_OPTIMISM
python scripts/erc-4626/scan-vaults.py
if [[ "$SCAN_PRICES" == "true" ]]; then 
    python scripts/erc-4626/scan-prices.py ; 
fi

echo "Cleaning vault data"
python scripts/erc-4626/clean-prices.py

echo "Creating sparkline images"
python scripts/erc-4626/export-sparklines.py

echo "All done"
