#!/bin/bash
#
# This script is to stress test RPC provider using ERC-4626 vault data scanner.
#
# The vault data scanner runs a real-world workload against an archive node RPC provider.
# It issues requests in ranges on ~10k-3M per run (depends on chain and data)
# and tends to cover multiple rare error modes and edge cases, especially
# what comes to the RPC upstream node load handling and robustness.
# Calls done include eth_call, eth_getLogs, eth_getBlockByNumber, multicall calls etc.
#
# The scripts have default RPC retries of 5 before giving up.
# It should give verbose çonsole diagnostics about failing RPC requests before giving up.
# So when your RPC is faulty, it will crash with fireworks and good error messages.
#
# Make sure you have allocated at least 16 GB RAM for your Docker engine (Mac).
#
# Usage example (after cloning the repo).
# We recommend giving Arbitrum RPC for maximum fireworks:
#
#     export JSON_RPC_URL="<...>"
#     ./rpc-test.sh
#
# Best chain to stress test is Arbitrum as it has a lot of blocks and vaults.
#
# - Dependencies needed: Docker, docker compose (available as built in docker add-on).
# - No Python installation needed as everything runs in Docker container.
# - Running Docker on Linux is 3x - 5x faster.
# - For supported chains see docker-compose.yml
# - All long running tasks have an interactive progress bar and time estimates.
#
# Host holders used for data ~/.tradingstrategy and ~/.cache/tradingstrategy
# - delete these folders to start the restart the scan from block 1.
#
# If the process dies without a reason, Docker likely runs out of RAM.
# Some chains like Arbitrum with a lot of blocks may require more RAM.
#

# How many worker process to spawn for parallel processing
# (limited by RAM) - this values should be good for 16 GB RAM configured to Docker engine.
# If it is slow, remember to change your Docker Desktop / Docker Engine settings.
export MAX_WORKERS=32

# Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
# Set to "info" to get a lot of output.
export LOG_LEVEL=info

install -d ~/.tradingstrategy/
install -d ~/.cache/

if [ -z "${JSON_RPC_URL:-}" ]; then
  echo "Please set JSON_RPC_URL environment variable"
  exit 1
fi

set -e
set -u
set -x

# Step 1. build the Docker image hosting the Python app
echo "👉 1. Building container"
docker compose build vault-scanner

# Step 2. run vault detection based on event (log) analysis
# and extract  metadata using Multicall3.
# SCAN_BACKEND=rpc disables Hypersync support and forces to use slow RPC path everywhere.
echo "👉 2. Scanning vault detection events and metadata"
docker compose run \
  --env SCAN_BACKEND=rpc \
  --env JSON_RPC_URL \
  --env MAX_WORKERS \
  --env LOG_LEVEL \
  --entrypoint python vault-scanner \
  -- \
  scripts/erc-4626/scan-vaults.py

# Step 3. run vault historical price scan against archive node historical blocks
echo "👉 3. Scanning historical vault price data from an achive node"
docker compose run \
  --env SCAN_BACKEND=rpc \
  --env JSON_RPC_URL \
  --env MAX_WORKERS \
  --env LOG_LEVEL \
  --entrypoint python vault-scanner \
  -- \
  scripts/erc-4626/scan-prices.py

