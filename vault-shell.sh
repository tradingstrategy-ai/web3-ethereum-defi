#!/bin/bash
#
# Spawn a bash shell inside the vault-scanner-looped container.
#
# Use this to run ad-hoc scripts or debug inside the running container
# instead of bind-mounting ./scripts from the host, which can cause
# import mismatches between host scripts and the baked-in eth_defi package.
#
# Usage:
#   ./vault-shell.sh
#
# Then inside the container you can run e.g.:
#   python scripts/erc-4626/export-data-files.py
#
set -euo pipefail

CONTAINER="${1:-vault-scanner-looped}"

exec docker compose exec "$CONTAINER" /bin/bash
