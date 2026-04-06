"""Standalone post-processing pipeline for vault price data.

Merges native protocol data (Hypercore, GRVT, Lighter) into the
uncleaned parquet, cleans the data, and uploads to R2. Each step
reports success/failure and the script exits with code 1 if any
step fails.

Use this to debug post-processing issues independently of the
full chain scanning pipeline.

Usage:

.. code-block:: shell

    # Run full post-processing (merge + clean + export)
    source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py

    # Skip native protocol merges (just clean + export)
    source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py

    # Include Hypercore, GRVT, Lighter merges
    source .local-test.env && \
    MERGE_HYPERCORE=true MERGE_GRVT=true MERGE_LIGHTER=true \
    poetry run python scripts/erc-4626/post-process-prices.py

    # Only merge and clean, skip all R2 uploads
    SKIP_SPARKLINES=true SKIP_METADATA=true SKIP_DATA=true \
    poetry run python scripts/erc-4626/post-process-prices.py

    # Skip only data file upload
    SKIP_DATA=true poetry run python scripts/erc-4626/post-process-prices.py

    # Test upload (prefixes uploaded filenames with "test-")
    source .local-test.env && \
    UPLOAD_PREFIX=test- poetry run python scripts/erc-4626/post-process-prices.py

Environment variables:

Pipeline control:

- ``LOG_LEVEL``: Logging level (default: ``info``)
- ``MERGE_HYPERCORE``: Merge Hyperliquid native vault data (default: ``false``)
- ``MERGE_GRVT``: Merge GRVT native vault data (default: ``false``)
- ``MERGE_LIGHTER``: Merge Lighter native pool data (default: ``false``)
- ``SKIP_CLEANING``: Skip price cleaning step (default: ``false``)
- ``SKIP_SPARKLINES``: Skip sparkline image export to R2 (default: ``false``)
- ``SKIP_METADATA``: Skip protocol/stablecoin metadata export to R2 (default: ``false``)
- ``SKIP_DATA``: Skip data file (parquet, pickle) export to R2 (default: ``false``)
- ``UPLOAD_PREFIX``: Prefix for uploaded data file keys, e.g. ``test-`` (default: ``""``)
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
"""

import logging
import os
import sys

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.post_processing import (
    clean_prices,
    export_data_files,
    export_protocol_metadata,
    export_sparklines,
    merge_native_protocols,
)

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    merge_hypercore = os.environ.get("MERGE_HYPERCORE", "false").lower() == "true"
    merge_grvt = os.environ.get("MERGE_GRVT", "false").lower() == "true"
    merge_lighter = os.environ.get("MERGE_LIGHTER", "false").lower() == "true"
    skip_cleaning = os.environ.get("SKIP_CLEANING", "false").lower() == "true"
    skip_sparklines = os.environ.get("SKIP_SPARKLINES", "false").lower() == "true"
    skip_metadata = os.environ.get("SKIP_METADATA", "false").lower() == "true"
    skip_data = os.environ.get("SKIP_DATA", "false").lower() == "true"

    steps = {}

    # Step 1: Merge native protocols
    logger.info("Step 1: Merging native protocol data")
    merge_results = merge_native_protocols(
        merge_hypercore=merge_hypercore,
        merge_grvt=merge_grvt,
        merge_lighter=merge_lighter,
    )
    steps.update(merge_results)
    if not any([merge_hypercore, merge_grvt, merge_lighter]):
        logger.info("No native protocol merges requested (set MERGE_HYPERCORE/MERGE_GRVT/MERGE_LIGHTER=true)")

    # Step 2: Clean prices
    if skip_cleaning:
        logger.info("Step 2: Skipping price cleaning (SKIP_CLEANING=true)")
    else:
        logger.info("Step 2: Cleaning prices")
        steps["clean-prices"] = clean_prices()

    # Step 3: Export sparklines to R2
    if skip_sparklines:
        logger.info("Step 3: Skipping sparkline export (SKIP_SPARKLINES=true)")
    else:
        logger.info("Step 3: Exporting sparklines")
        steps["export-sparklines"] = export_sparklines()

    # Step 4: Export protocol metadata to R2
    if skip_metadata:
        logger.info("Step 4: Skipping metadata export (SKIP_METADATA=true)")
    else:
        logger.info("Step 4: Exporting protocol metadata")
        steps["export-protocol-metadata"] = export_protocol_metadata()

    # Step 5: Export data files to R2
    if skip_data:
        logger.info("Step 5: Skipping data file export (SKIP_DATA=true)")
    else:
        logger.info("Step 5: Exporting data files")
        steps["export-data-files"] = export_data_files()

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
