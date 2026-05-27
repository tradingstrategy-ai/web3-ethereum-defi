#!/usr/bin/env bash
# Classify test files as unit (no fork, no live RPC).
# Output: sorted list of test file paths, one per line.
# Usage: bash scripts/ci/list-unit-tests.sh > tests/unit-manifest.txt
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# Collect all test IDs; exit code 2 = collection errors on some files — ignore
poetry run pytest tests/ --collect-only -q --ignore=tests/gmx 2>/dev/null > "$TMPFILE" || true

# Extract unique file paths (lines like tests/foo/test_bar.py::test_fn)
grep -E '^tests/.*\.py::' "$TMPFILE" \
  | awk -F'::' '{print $1}' \
  | sort -u \
  | while read -r f; do
      [ -f "$f" ] || continue
      if ! grep -qE \
        "mainnet_fork|web3_fork|anvil|JSON_RPC_|HYPERSYNC_API_KEY|GCP_ADC_CREDENTIALS|web3_arbitrum_fork|web3_ethereum_fork|web3_base_fork|web3_polygon_fork|web3_bnb_fork|web3_hyperliquid_fork" \
        "$f" 2>/dev/null; then
        echo "$f"
      fi
    done
