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

    # Only merge and clean, skip R2 upload
    SKIP_EXPORT=true poetry run python scripts/erc-4626/post-process-prices.py

    # Test upload (prefixes uploaded filenames with "test-")
    source .local-test.env && \
    UPLOAD_PREFIX=test- poetry run python scripts/erc-4626/post-process-prices.py

Environment variables:

- ``MERGE_HYPERCORE``: Merge Hyperliquid native vault data (default: false)
- ``MERGE_GRVT``: Merge GRVT native vault data (default: false)
- ``MERGE_LIGHTER``: Merge Lighter native pool data (default: false)
- ``SKIP_EXPORT``: Skip sparkline and metadata export to R2 (default: false)
- ``UPLOAD_PREFIX``: Prefix for uploaded data file keys, e.g. ``test-`` (default: "")
- ``LOG_LEVEL``: Logging level (default: info)
"""

import logging
import os
import sys

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.post_processing import (
    clean_prices,
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
    skip_export = os.environ.get("SKIP_EXPORT", "false").lower() == "true"

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
    logger.info("Step 2: Cleaning prices")
    steps["clean-prices"] = clean_prices()

    # Step 3: Export to R2
    if skip_export:
        logger.info("Step 3: Skipping export (SKIP_EXPORT=true)")
    else:
        logger.info("Step 3a: Exporting sparklines")
        steps["export-sparklines"] = export_sparklines()

        logger.info("Step 3b: Exporting protocol metadata and database files")
        steps["export-protocol-metadata"] = export_protocol_metadata()

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
