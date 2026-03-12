"""Stage 1: Extract vault data from S3 archive LZ4 files into staging DuckDB.

Processes ``account_values/*.csv.lz4`` files, extracts vault-only
rows (``is_vault=true``), and stores them in a staging DuckDB for later backfill.

Supports two modes:

1. **Direct S3 download** (recommended): Set ``AWS_ACCESS_KEY_ID`` and
   ``AWS_SECRET_ACCESS_KEY`` and the script downloads files directly from S3
   to a local cache, then extracts.

2. **Pre-downloaded files**: Set ``S3_DATA_DIR`` to point to a directory with
   previously downloaded ``.csv.lz4`` files.

Resumable: skips dates already in the staging database. Optionally deletes LZ4
files after extraction to save disc space.

Usage:

.. code-block:: shell

    # Direct S3 download (recommended)
    AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... \\
        poetry run python scripts/hyperliquid/extract-s3-vault-data.py

    # With pre-downloaded files
    S3_DATA_DIR=~/hl-archive/account_values/ \\
        poetry run python scripts/hyperliquid/extract-s3-vault-data.py

    # Extract specific date range without deleting files
    AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... \\
    START_DATE=2025-11-01 END_DATE=2026-01-31 DELETE_LZ4=false \\
        poetry run python scripts/hyperliquid/extract-s3-vault-data.py

Environment variables:

- ``AWS_ACCESS_KEY_ID``: AWS access key ID for S3 download
- ``AWS_SECRET_ACCESS_KEY``: AWS secret access key for S3 download
- ``S3_DATA_DIR``: Directory with ``.csv.lz4`` files (skips S3 download if set)
- ``S3_DOWNLOAD_DIR``: Where to cache downloaded LZ4 files.
  Default: ``~/hl-archive/account_values/``
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

#: Default local cache for downloaded S3 files
DEFAULT_S3_DOWNLOAD_DIR = Path("~/hl-archive/account_values/").expanduser()


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    staging_db_path_str = os.environ.get("STAGING_DB_PATH")
    staging_db_path = Path(staging_db_path_str).expanduser() if staging_db_path_str else HYPERLIQUID_S3_STAGING_DATABASE

    start_date_str = os.environ.get("START_DATE")
    start_date = datetime.date.fromisoformat(start_date_str) if start_date_str else None

    end_date_str = os.environ.get("END_DATE")
    end_date = datetime.date.fromisoformat(end_date_str) if end_date_str else None

    delete_lz4 = os.environ.get("DELETE_LZ4", "true").lower() in ("true", "1", "yes")

    # Determine data source: pre-downloaded directory or S3 download
    s3_data_dir_str = os.environ.get("S3_DATA_DIR")

    if s3_data_dir_str:
        # Mode 1: Use pre-downloaded files
        s3_data_dir = Path(s3_data_dir_str).expanduser()
        if not s3_data_dir.is_dir():
            raise ValueError(f"S3_DATA_DIR does not exist or is not a directory: {s3_data_dir}")
        print(f"Using pre-downloaded files from: {s3_data_dir}")
    else:
        # Mode 2: Download from S3 using AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
        from eth_defi.hyperliquid.backfill import configure_aws_credentials, download_s3_files

        configure_aws_credentials()

        download_dir_str = os.environ.get("S3_DOWNLOAD_DIR")
        s3_data_dir = Path(download_dir_str).expanduser() if download_dir_str else DEFAULT_S3_DOWNLOAD_DIR

        print(f"Downloading S3 files to: {s3_data_dir}")
        downloaded = download_s3_files(
            output_dir=s3_data_dir,
            start_date=start_date,
            end_date=end_date,
        )
        print(f"Downloaded {downloaded} new files")
        print()

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
