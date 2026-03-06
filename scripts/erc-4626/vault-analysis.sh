#!/bin/bash
# Run vault analysis for Arbitrum and Base using vault-export.json in /root/.tradingstrategy/vaults

set -e

source .venv/bin/activate

echo "Export to json file"
python scripts/erc-4626/vault-analysis-json.py
#cp /root/top_vaults_analysis.json ~/.tradingstrategy/top-stablecoin-vaults.json
