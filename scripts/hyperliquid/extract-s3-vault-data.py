"""Stage 1: Extract vault data from S3 archive LZ4 files into staging DuckDB.

Processes pre-downloaded ``account_values/*.csv.lz4`` files, extracts vault-only
rows (``is_vault=true``), and stores them in a staging DuckDB for later backfill.

Resumable: skips dates already in the staging database. Optionally deletes LZ4
files after extraction to save disc space.

Usage:

.. code-block:: shell

    # First download the S3 files (requires AWS credentials)
    aws s3 sync s3://hyperliquid-archive/account_values/ ~/hl-archive/account_values/ \\
        --request-payer requester

    # Then extract vault data
    S3_DATA_DIR=~/hl-archive/account_values/ \\
        poetry run python scripts/hyperliquid/extract-s3-vault-data.py

    # Extract specific date range without deleting files
    S3_DATA_DIR=~/hl-archive/account_values/ \\
    START_DATE=2025-11-01 END_DATE=2026-01-31 DELETE_LZ4=false \\
        poetry run python scripts/hyperliquid/extract-s3-vault-data.py

Environment variables:

- ``S3_DATA_DIR``: Directory with ``.csv.lz4`` files (required)
- ``STAGING_DB_PATH``: Staging DuckDB path.
  Default: ``~/.tradingstrategy/hyperliquid/s3-vault-backfill.duckdb``
- ``START_DATE``: Only process files from this date (YYYY-MM-DD)
- ``END_DATE``: Only process files up to this date (YYYY-MM-DD)
- ``DELETE_LZ4``: Delete LZ4 files after extraction. Default: ``true``
- ``LOG_LEVEL``: Logging level. Default: ``warning``

"""

import datetime
import logging
import os
from pathlib import Path

from eth_defi.hyperliquid.backfill import HYPERLIQUID_S3_STAGING_DATABASE, run_s3_extract
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    s3_data_dir_str = os.environ.get("S3_DATA_DIR")
    if not s3_data_dir_str:
        raise ValueError("S3_DATA_DIR environment variable is required")
    s3_data_dir = Path(s3_data_dir_str).expanduser()
    if not s3_data_dir.is_dir():
        raise ValueError(f"S3_DATA_DIR does not exist or is not a directory: {s3_data_dir}")

    staging_db_path_str = os.environ.get("STAGING_DB_PATH")
    staging_db_path = Path(staging_db_path_str).expanduser() if staging_db_path_str else HYPERLIQUID_S3_STAGING_DATABASE

    start_date_str = os.environ.get("START_DATE")
    start_date = datetime.date.fromisoformat(start_date_str) if start_date_str else None

    end_date_str = os.environ.get("END_DATE")
    end_date = datetime.date.fromisoformat(end_date_str) if end_date_str else None

    delete_lz4 = os.environ.get("DELETE_LZ4", "true").lower() in ("true", "1", "yes")

    print(f"Hyperliquid S3 vault data extraction (Stage 1)")
    print(f"S3 data directory: {s3_data_dir}")
    print(f"Staging DB path: {staging_db_path}")
    if start_date:
        print(f"Start date: {start_date}")
    if end_date:
        print(f"End date: {end_date}")
    print(f"Delete LZ4 after extraction: {delete_lz4}")

    lz4_count = len(list(s3_data_dir.glob("*.csv.lz4")))
    print(f"LZ4 files found: {lz4_count}")
    print()

    result = run_s3_extract(
        staging_db_path=staging_db_path,
        s3_data_dir=s3_data_dir,
        start_date=start_date,
        end_date=end_date,
        delete_lz4=delete_lz4,
    )

    print(f"\nExtraction complete:")
    print(f"  Dates processed: {result['dates_processed']}")
    print(f"  Dates skipped (already extracted): {result['dates_skipped']}")
    print(f"  Vault rows extracted: {result['vault_rows']:,}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
