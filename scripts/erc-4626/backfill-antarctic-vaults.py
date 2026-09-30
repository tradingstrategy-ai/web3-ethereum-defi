"""Manually backfill AMLP/AHLP metadata and full onchain settlement prices.

The fixed two-address scope updates the metadata pickle, historical context
DuckDB and raw price Parquet beneath ``PIPELINE_DATA_DIR``. It never resets
reader state. Rehearse first with the default ``DRY_RUN=true``, then set
``DRY_RUN=false`` to apply. Requires JSON_RPC_ARBITRUM and HYPERSYNC_API_KEY;
MAX_WORKERS defaults to 4. HYPERSYNC_RPM must match the supplied token quota.

Example::

    source .local-test.env
    DRY_RUN=true HYPERSYNC_RPM=20 poetry run python scripts/erc-4626/backfill-antarctic-vaults.py

Stop the production scanner and retain its standard mounted state before
applying. Run normal pipeline post-processing afterwards to refresh cleaned
prices and JSON. See the protocol README for production operations.
"""

from eth_defi.erc_4626.vault_protocol.antarctic.migration import main

if __name__ == "__main__":
    main()
