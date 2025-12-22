#!/bin/bash
# Run vault analysis for Arbitrum and Base using vault-export.json in /root/.tradingstrategy/vaults

set -e

source .venv/bin/activate

# Run Arbitrum
echo "Running Arbitrum analysis..."
export CHAIN_ID=42161
export GS_WORKSHEET_NAME="Arbitrum-vault-data"
python scripts/erc-4626/vault-analysis-gsheet.py --service-account-file "$GS_SERVICE_ACCOUNT_FILE"

echo
# Run Base
echo "Running Base analysis..."
export CHAIN_ID=8453
export GS_WORKSHEET_NAME="Base-vault-data"
python scripts/erc-4626/vault-analysis-gsheet.py --service-account-file "$GS_SERVICE_ACCOUNT_FILE"

echo
echo "Done: both Arbitrum and Base analyses completed."

echo "Export to json file"
python scripts/erc-4626/vault-analysis-json.py
#cp /root/top_vaults_analysis.json ~/.tradingstrategy/top-stablecoin-vaults.json
