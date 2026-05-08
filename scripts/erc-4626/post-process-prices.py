"""Standalone post-processing pipeline for vault price data.

Merges native protocol data (Hypercore, GRVT, Lighter, Hibachi) into the
uncleaned parquet, cleans the data, generates the top-vaults JSON,
and uploads to R2. Each step reports success/failure and the script
exits with code 1 if any step fails.

This script is a thin wrapper around
:py:func:`eth_defi.vault.post_processing.run_post_processing` so it
stays in lockstep with the production scanner pipeline — any new step
added to ``run_post_processing()`` automatically shows up here with no
drift.

Use this to debug post-processing issues independently of the
full chain scanning pipeline.

Usage:

.. code-block:: shell

    # Run full post-processing (merge + clean + export)
    source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py

    # Skip native protocol merges (just clean + export)
    source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py

    # Include Hypercore, GRVT, Lighter, Hibachi merges
    source .local-test.env && \\
    MERGE_HYPERCORE=true MERGE_GRVT=true MERGE_LIGHTER=true MERGE_HIBACHI=true \\
    poetry run python scripts/erc-4626/post-process-prices.py

    # Only merge and clean, skip all R2 uploads
    SKIP_SPARKLINES=true SKIP_METADATA=true SKIP_DATA=true SKIP_TOP_VAULTS=true \\
    poetry run python scripts/erc-4626/post-process-prices.py

    # Skip only data file upload
    SKIP_DATA=true poetry run python scripts/erc-4626/post-process-prices.py

    # Test upload (prefixes uploaded filenames with "test-")
    source .local-test.env && \\
    UPLOAD_PREFIX=test- poetry run python scripts/erc-4626/post-process-prices.py

Environment variables:

Pipeline control:

- ``LOG_LEVEL``: Logging level (default: ``info``)
- ``PIPELINE_DATA_DIR``: Override pipeline data directory (default: ``~/.tradingstrategy/vaults``)
- ``MERGE_HYPERCORE``: Merge Hyperliquid native vault data (default: ``false``)
- ``MERGE_GRVT``: Merge GRVT native vault data (default: ``false``)
- ``MERGE_LIGHTER``: Merge Lighter native pool data (default: ``false``)
- ``MERGE_HIBACHI``: Merge Hibachi native vault data (default: ``false``)
- ``SKIP_CLEANING``: Skip price cleaning step (default: ``false``)
- ``SKIP_TOP_VAULTS``: Skip top-vaults JSON generation and R2 upload (default: ``false``)
- ``SKIP_SPARKLINES``: Skip sparkline image export to R2 (default: ``false``)
- ``SKIP_METADATA``: Skip protocol/stablecoin metadata export to R2 (default: ``false``)
- ``SKIP_DATA``: Skip data file (parquet, pickle) export to R2 (default: ``false``)
- ``SKIP_SAMPLES``: Skip Ethereum-only sample file export to R2 (default: ``false``)
- ``UPLOAD_PREFIX``: Prefix for uploaded data file keys, e.g. ``test-`` (default: ``""``). Applies to all R2 uploads including the top-vaults JSON.
- ``MAX_WORKERS``: Number of parallel workers for rendering/uploading (default: ``20``)

Sparkline R2 bucket (required unless ``SKIP_SPARKLINES=true``):

- ``R2_SPARKLINE_BUCKET_NAME``: R2 bucket for sparkline images
- ``R2_SPARKLINE_ACCESS_KEY_ID``: R2 access key ID for sparkline bucket
- ``R2_SPARKLINE_SECRET_ACCESS_KEY``: R2 secret access key for sparkline bucket
- ``R2_SPARKLINE_ENDPOINT_URL``: R2 endpoint URL for sparkline bucket

Protocol metadata R2 bucket (required unless ``SKIP_METADATA=true``):

- ``R2_VAULT_METADATA_BUCKET_NAME``: R2 bucket for protocol/stablecoin metadata and logos
- ``R2_VAULT_METADATA_ACCESS_KEY_ID``: R2 access key ID for metadata bucket
- ``R2_VAULT_METADATA_SECRET_ACCESS_KEY``: R2 secret access key for metadata bucket
- ``R2_VAULT_METADATA_ENDPOINT_URL``: R2 endpoint URL for metadata bucket
- ``R2_VAULT_METADATA_PUBLIC_URL``: Public base URL for logo URLs in metadata

Alternative R2 bucket (optional, for the upcoming private commercial professional vault data bucket):

- ``R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME``: When set, metadata and data files are uploaded
  to both the primary and this alternative bucket using the same credentials

Data files R2 bucket (falls back to ``R2_VAULT_METADATA_*`` if not set):

- ``R2_DATA_BUCKET_NAME``: R2 bucket for parquet/pickle data files
- ``R2_DATA_ACCESS_KEY_ID``: R2 access key ID for data bucket
- ``R2_DATA_SECRET_ACCESS_KEY``: R2 secret access key for data bucket
- ``R2_DATA_ENDPOINT_URL``: R2 endpoint URL for data bucket
- ``R2_DATA_PUBLIC_URL``: Public base URL for data files

Top-vaults JSON R2 bucket (required unless ``SKIP_TOP_VAULTS=true``):

- ``R2_TOP_VAULTS_BUCKET_NAME``: R2 bucket for ``top_vaults_by_chain.json`` (primary public bucket, e.g. ``top-defi-vaults.tradingstrategy.ai``)
- ``R2_TOP_VAULTS_ACCESS_KEY_ID``: R2 access key ID for top-vaults bucket
- ``R2_TOP_VAULTS_SECRET_ACCESS_KEY``: R2 secret access key for top-vaults bucket
- ``R2_TOP_VAULTS_ENDPOINT_URL``: R2 endpoint URL for top-vaults bucket
- ``R2_TOP_VAULTS_PUBLIC_URL``: Optional public base URL for the top-vaults JSON
- ``R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME``: Optional alternative (private) bucket. When set, the JSON is uploaded to both primary and alternative using the same credentials.
"""

import logging
import os
import sys

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.post_processing import run_post_processing, validate_top_vaults_config
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    merge_hypercore = os.environ.get("MERGE_HYPERCORE", "false").lower() == "true"
    merge_grvt = os.environ.get("MERGE_GRVT", "false").lower() == "true"
    merge_lighter = os.environ.get("MERGE_LIGHTER", "false").lower() == "true"
    merge_hibachi = os.environ.get("MERGE_HIBACHI", "false").lower() == "true"
    skip_cleaning = os.environ.get("SKIP_CLEANING", "false").lower() == "true"
    skip_top_vaults = os.environ.get("SKIP_TOP_VAULTS", "false").lower() == "true"
    skip_sparklines = os.environ.get("SKIP_SPARKLINES", "false").lower() == "true"
    skip_metadata = os.environ.get("SKIP_METADATA", "false").lower() == "true"
    skip_data = os.environ.get("SKIP_DATA", "false").lower() == "true"
    skip_samples = os.environ.get("SKIP_SAMPLES", "false").lower() == "true"

    # Fail-fast: crash with a clear error before we touch anything if the
    # top-vaults R2 upload is not configured. Matches scan-vaults-all-chains.py.
    validate_top_vaults_config(skip_top_vaults=skip_top_vaults)

    # Compute all pipeline paths from the shared data directory so
    # PIPELINE_DATA_DIR is honoured identically to the production scanner.
    # Without explicit paths, run_post_processing() falls back to the
    # frozen module-level defaults resolved at import time from Path.home(),
    # which silently ignore PIPELINE_DATA_DIR.
    data_dir = get_pipeline_data_dir()
    vault_db_path = data_dir / "vault-metadata-db.pickle"
    uncleaned_price_path = data_dir / "vault-prices-1h.parquet"
    cleaned_price_path = data_dir / "cleaned-vault-prices-1h.parquet"
    hyperliquid_db_path = data_dir / "hyperliquid-vaults.duckdb"
    hyperliquid_hf_db_path = data_dir / "hyperliquid-vaults-hf.duckdb"
    grvt_db_path = data_dir / "grvt-vaults.duckdb"
    lighter_db_path = data_dir / "lighter-pools.duckdb"
    hibachi_db_path = data_dir / "hibachi-vaults.duckdb"

    logger.info("Pipeline data directory: %s", data_dir)
    if not any([merge_hypercore, merge_grvt, merge_lighter, merge_hibachi]):
        logger.info("No native protocol merges requested (set MERGE_HYPERCORE/MERGE_GRVT/MERGE_LIGHTER/MERGE_HIBACHI=true)")

    # run_post_processing() uses scan_hypercore/scan_grvt/scan_lighter for
    # the "merge this native protocol's data" flags. We keep the
    # MERGE_* env var names that this debug script has always documented
    # and map them at the call site — no behavioural change for operators.
    steps = run_post_processing(
        scan_hypercore=merge_hypercore,
        scan_grvt=merge_grvt,
        scan_lighter=merge_lighter,
        scan_hibachi=merge_hibachi,
        skip_cleaning=skip_cleaning,
        skip_top_vaults=skip_top_vaults,
        skip_sparklines=skip_sparklines,
        skip_metadata=skip_metadata,
        skip_data=skip_data,
        skip_samples=skip_samples,
        uncleaned_parquet_path=uncleaned_price_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=grvt_db_path,
        lighter_db_path=lighter_db_path,
        hibachi_db_path=hibachi_db_path,
        vault_db_path=vault_db_path,
        cleaned_path=cleaned_price_path,
    )

    # Summary
    rows = []
    for step_name, success in steps.items():
        rows.append([step_name, "OK" if success else "FAILED"])

    print(f"\n{tabulate(rows, headers=['Step', 'Status'], tablefmt='fancy_grid')}")

    failed = [name for name, success in steps.items() if not success]
    if failed:
        print(f"\nFAILED steps: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("\nAll steps completed successfully")


if __name__ == "__main__":
    main()
