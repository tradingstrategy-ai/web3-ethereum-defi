"""Hyperliquid vault data backfill from S3 archive.

Two-stage pipeline for backfilling daily vault data from the
``s3://hyperliquid-archive/account_values/`` S3 prefix.

**Stage 1 — Extract**: Download LZ4 files, extract vault-only rows into a
staging DuckDB, delete the LZ4 files. Resumable — skips already-processed dates.

**Stage 2 — Apply**: Read from staging DuckDB, insert missing dates into the
main ``daily-metrics.duckdb``, recompute share prices.

The S3 ``account_values`` files contain daily snapshots for every address
on Hyperliquid with columns: ``time, user, is_vault, account_value, cum_vlm, cum_ledger``.

From these we derive:

- ``tvl = account_value``
- ``cumulative_pnl = account_value - cum_ledger``
- ``daily_pnl = cumulative_pnl[i] - cumulative_pnl[i-1]``

Then ``recompute_vault_share_prices()`` computes share prices from the stored data.

See :doc:`/scripts/hyperliquid/README-hyperliquid-backfill` for full documentation.
"""

import csv
import datetime
import io
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path

import duckdb
import lz4.frame
import pandas as pd
from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase

logger = logging.getLogger(__name__)

#: S3 bucket for the Hyperliquid archive
HYPERLIQUID_S3_BUCKET = "hyperliquid-archive"

#: S3 prefix for account_values files
HYPERLIQUID_S3_PREFIX = "account_values/"

#: Default path for the S3 vault backfill staging database
HYPERLIQUID_S3_STAGING_DATABASE = Path("~/.tradingstrategy/hyperliquid/s3-vault-backfill.duckdb").expanduser()

#: Regex to extract date from S3 filenames like ``20260301.csv.lz4``
S3_FILENAME_PATTERN = re.compile(r"(\d{8})\.csv\.lz4$")


class HyperliquidS3StagingDatabase:
    """Staging database for vault data extracted from S3 archive.

    Stores vault-only rows from the S3 ``account_values`` files and
    tracks which dates have been processed for resumable extraction.

    :param db_path:
        Path to the DuckDB staging database file.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(db_path))
        self._init_schema()

    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_account_values (
                date DATE NOT NULL,
                vault_address VARCHAR NOT NULL,
                account_value DOUBLE NOT NULL,
                cum_ledger DOUBLE NOT NULL,
                cum_vlm DOUBLE,
                PRIMARY KEY (vault_address, date)
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS processed_dates (
                date DATE PRIMARY KEY,
                vault_rows INTEGER NOT NULL,
                processed_at TIMESTAMP NOT NULL
            )
        """)

    def is_date_processed(self, date: datetime.date) -> bool:
        """Check if a date's S3 file has already been extracted."""
        result = self.con.execute(
            "SELECT 1 FROM processed_dates WHERE date = ?",
            [date],
        ).fetchone()
        return result is not None

    def mark_date_processed(self, date: datetime.date, vault_rows: int):
        """Mark a date as successfully processed.

        :param date:
            The date that was processed.
        :param vault_rows:
            Number of vault rows extracted from this date's file.
        """
        from eth_defi.compat import native_datetime_utc_now

        self.con.execute(
            """
            INSERT INTO processed_dates (date, vault_rows, processed_at)
            VALUES (?, ?, ?)
            ON CONFLICT (date) DO UPDATE SET
                vault_rows = EXCLUDED.vault_rows,
                processed_at = EXCLUDED.processed_at
            """,
            [date, vault_rows, native_datetime_utc_now()],
        )

    def get_processed_dates(self) -> set[datetime.date]:
        """Get all dates that have been processed."""
        rows = self.con.execute("SELECT date FROM processed_dates").fetchall()
        return {r[0] for r in rows}

    def insert_vault_rows(self, rows: list[tuple]):
        """Insert vault data rows into the staging table.

        :param rows:
            List of tuples: ``(date, vault_address, account_value, cum_ledger, cum_vlm)``
        """
        if not rows:
            return
        self.con.executemany(
            """
            INSERT INTO vault_account_values (date, vault_address, account_value, cum_ledger, cum_vlm)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (vault_address, date) DO UPDATE SET
                account_value = EXCLUDED.account_value,
                cum_ledger = EXCLUDED.cum_ledger,
                cum_vlm = EXCLUDED.cum_vlm
            """,
            rows,
        )

    def get_vault_data(self, vault_address: str) -> pd.DataFrame:
        """Get all staged data for a specific vault, ordered by date.

        :param vault_address:
            Vault address (will be lowercased).
        :return:
            DataFrame with columns: date, vault_address, account_value, cum_ledger, cum_vlm
        """
        return self.con.execute(
            """
            SELECT date, vault_address, account_value, cum_ledger, cum_vlm
            FROM vault_account_values
            WHERE vault_address = ?
            ORDER BY date
            """,
            [vault_address.lower()],
        ).df()

    def get_all_vault_addresses(self) -> list[str]:
        """Get all unique vault addresses in the staging database."""
        rows = self.con.execute("SELECT DISTINCT vault_address FROM vault_account_values ORDER BY vault_address").fetchall()
        return [r[0] for r in rows]

    def get_vault_count(self) -> int:
        """Get number of unique vaults in staging database."""
        return self.con.execute("SELECT COUNT(DISTINCT vault_address) FROM vault_account_values").fetchone()[0]

    def get_total_rows(self) -> int:
        """Get total number of vault data rows."""
        return self.con.execute("SELECT COUNT(*) FROM vault_account_values").fetchone()[0]

    def save(self):
        """Commit pending changes."""
        self.con.commit()

    def close(self):
        """Close the database connection."""
        self.con.close()


def parse_s3_filename_date(filename: str) -> datetime.date | None:
    """Extract date from an S3 account_values filename.

    :param filename:
        Filename like ``20260301.csv.lz4``
    :return:
        Date object, or None if filename doesn't match the pattern.
    """
    match = S3_FILENAME_PATTERN.search(filename)
    if not match:
        return None
    date_str = match.group(1)
    return datetime.date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))


def configure_aws_credentials():
    """Validate that AWS credentials are available for S3 access.

    Accepts either explicit environment variables or a named profile:

    - ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` (+ ``AWS_SESSION_TOKEN`` for MFA)
    - ``AWS_PROFILE`` — named profile from ``~/.aws/credentials``

    Also sets the default region to ``eu-west-1`` if not already configured
    (the ``hyperliquid-archive`` S3 bucket is in ``eu-west-1``).

    :raises ValueError:
        If neither ``AWS_ACCESS_KEY_ID`` nor ``AWS_PROFILE`` is set.
    """
    # Default region — the hyperliquid-archive bucket is in eu-west-1
    os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

    if not os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("AWS_PROFILE"):
        raise ValueError("AWS credentials not found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables, or set AWS_PROFILE to use a named profile from ~/.aws/credentials.")


def download_s3_files(
    output_dir: Path,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> int:
    """Download account_values LZ4 files from the Hyperliquid S3 archive.

    Downloads files from ``s3://hyperliquid-archive/account_values/`` to the
    specified output directory. Skips files that already exist locally.
    Requires AWS credentials to be configured (via :func:`configure_aws_credentials`
    or standard AWS environment variables).

    :param output_dir:
        Local directory to download files into.
    :param start_date:
        Only download files from this date onwards.
    :param end_date:
        Only download files up to this date.
    :return:
        Number of files downloaded.
    """
    import boto3

    output_dir.mkdir(parents=True, exist_ok=True)

    s3 = boto3.client("s3")

    # List all objects in the prefix
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=HYPERLIQUID_S3_BUCKET,
        Prefix=HYPERLIQUID_S3_PREFIX,
        RequestPayer="requester",
    )

    files_to_download = []
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/")[-1]
            if not filename.endswith(".csv.lz4"):
                continue

            file_date = parse_s3_filename_date(filename)
            if file_date is None:
                continue
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue

            local_path = output_dir / filename
            if local_path.exists():
                logger.debug("Skipping already downloaded: %s", filename)
                continue

            files_to_download.append((key, local_path, obj["Size"]))

    if not files_to_download:
        logger.info("No new files to download")
        return 0

    downloaded = 0
    progress = tqdm(
        files_to_download,
        desc="Downloading S3 files",
        unit="file",
    )

    total_bytes = 0
    for key, local_path, size in progress:
        s3.download_file(
            Bucket=HYPERLIQUID_S3_BUCKET,
            Key=key,
            Filename=str(local_path),
            ExtraArgs={"RequestPayer": "requester"},
        )
        total_bytes += size
        downloaded += 1
        progress.set_postfix(
            downloaded=downloaded,
            size=f"{total_bytes / 1024 / 1024:.1f}MB",
        )

    logger.info("Downloaded %d files (%.1f MB)", downloaded, total_bytes / 1024 / 1024)
    return downloaded


def parse_account_values_lz4(file_path: Path) -> Iterator[tuple]:
    """Decompress an LZ4 file and yield vault-only rows.

    Reads the S3 ``account_values`` CSV format, filters for ``is_vault=true``,
    and yields parsed tuples.

    :param file_path:
        Path to the ``.csv.lz4`` file.
    :return:
        Iterator of ``(date, vault_address, account_value, cum_ledger, cum_vlm)`` tuples.
    """
    file_date = parse_s3_filename_date(file_path.name)
    if file_date is None:
        raise ValueError(f"Cannot extract date from filename: {file_path.name}")

    with open(file_path, "rb") as f:
        compressed_data = f.read()

    decompressed = lz4.frame.decompress(compressed_data)
    text = decompressed.decode("utf-8")

    reader = csv.reader(io.StringIO(text))

    # Skip header if present
    first_row = next(reader, None)
    if first_row is None:
        return

    # Check if first row is a header (contains non-numeric 'time' field)
    if first_row[0].strip().lower() == "time":
        pass  # Header row, skip it
    else:
        # First row is data, process it
        yield from _process_csv_row(first_row, file_date)

    for row in reader:
        yield from _process_csv_row(row, file_date)


def _process_csv_row(row: list[str], file_date: datetime.date) -> Iterator[tuple]:
    """Process a single CSV row from account_values file.

    Schema: ``time, user, is_vault, account_value, cum_vlm, cum_ledger``

    :param row:
        CSV row as list of strings.
    :param file_date:
        Date extracted from the filename.
    """
    if len(row) < 6:
        return

    is_vault = row[2].strip().lower()
    if is_vault != "true":
        return

    vault_address = row[1].strip().lower()
    try:
        account_value = float(row[3].strip())
        cum_vlm = float(row[4].strip()) if row[4].strip() else None
        cum_ledger = float(row[5].strip())
    except (ValueError, IndexError):
        logger.warning("Failed to parse row for vault %s on %s: %s", vault_address, file_date, row)
        return

    yield (file_date, vault_address, account_value, cum_ledger, cum_vlm)


def run_s3_extract(
    staging_db_path: Path,
    s3_data_dir: Path,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    delete_lz4: bool = True,
) -> dict:
    """Stage 1: Extract vault data from S3 LZ4 files into staging DuckDB.

    Processes pre-downloaded ``.csv.lz4`` files from ``s3_data_dir``.
    Resumable — skips dates already in the staging database.
    Optionally deletes LZ4 files after successful extraction.

    :param staging_db_path:
        Path to the staging DuckDB database.
    :param s3_data_dir:
        Directory containing downloaded ``.csv.lz4`` files.
    :param start_date:
        Only process files from this date onwards.
    :param end_date:
        Only process files up to this date.
    :param delete_lz4:
        Delete LZ4 files after successful extraction.
    :return:
        Summary dict with ``dates_processed``, ``dates_skipped``, ``vault_rows``.
    """
    # Find all LZ4 files
    lz4_files = sorted(s3_data_dir.glob("*.csv.lz4"))
    if not lz4_files:
        logger.warning("No .csv.lz4 files found in %s", s3_data_dir)
        return {"dates_processed": 0, "dates_skipped": 0, "vault_rows": 0}

    # Parse dates and filter
    dated_files = []
    for f in lz4_files:
        file_date = parse_s3_filename_date(f.name)
        if file_date is None:
            logger.warning("Skipping file with unrecognised name: %s", f.name)
            continue
        if start_date and file_date < start_date:
            continue
        if end_date and file_date > end_date:
            continue
        dated_files.append((file_date, f))

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    try:
        processed_dates = staging_db.get_processed_dates()

        total_rows = 0
        dates_processed = 0
        dates_skipped = 0

        progress = tqdm(
            dated_files,
            desc="Extracting S3 vault data",
            unit="file",
        )

        for file_date, file_path in progress:
            if file_date in processed_dates:
                dates_skipped += 1
                progress.set_postfix(
                    processed=dates_processed,
                    skipped=dates_skipped,
                    rows=total_rows,
                    file=file_path.name,
                )
                continue

            file_size = file_path.stat().st_size
            rows = list(parse_account_values_lz4(file_path))
            staging_db.insert_vault_rows(rows)
            staging_db.mark_date_processed(file_date, len(rows))
            staging_db.save()

            if delete_lz4:
                file_path.unlink()

            total_rows += len(rows)
            dates_processed += 1

            progress.set_postfix(
                processed=dates_processed,
                skipped=dates_skipped,
                rows=total_rows,
                size=f"{file_size / 1024 / 1024:.1f}MB",
                vaults_today=len(rows),
            )
            logger.info(
                "Extracted %s: %d vault rows, %.1f MB compressed",
                file_path.name,
                len(rows),
                file_size / 1024 / 1024,
            )

        return {
            "dates_processed": dates_processed,
            "dates_skipped": dates_skipped,
            "vault_rows": total_rows,
        }
    finally:
        staging_db.close()


def apply_backfill_single_vault(
    staging_db: HyperliquidS3StagingDatabase,
    metrics_db: HyperliquidDailyMetricsDatabase,
    vault_address: HexAddress,
    overwrite_existing: bool = False,
) -> dict:
    """Apply staged S3 data for a single vault to the main metrics database.

    Inserts rows for dates not already present in the metrics database,
    then recomputes share prices across the full history.

    :param staging_db:
        Staging database with extracted S3 data.
    :param metrics_db:
        Main daily metrics database to insert into.
    :param vault_address:
        Vault address to backfill.
    :param overwrite_existing:
        If True, overwrite existing rows. If False (default), skip dates
        that already have data.
    :return:
        Summary dict with ``dates_found``, ``dates_inserted``, ``dates_skipped``.
    """
    vault_address = vault_address.lower()

    # Get staged data for this vault
    staged_df = staging_db.get_vault_data(vault_address)
    if staged_df.empty:
        return {"dates_found": 0, "dates_inserted": 0, "dates_skipped": 0}

    dates_found = len(staged_df)

    # Get existing dates in main DB
    if overwrite_existing:
        existing_dates = set()
    else:
        existing_dates = metrics_db.get_existing_dates(vault_address)

    # Build rows to insert: compute cumulative_pnl and daily_pnl from S3 data
    staged_df = staged_df.sort_values("date").reset_index(drop=True)

    rows_to_insert = []
    prev_cumulative_pnl = None

    for i, row in staged_df.iterrows():
        row_date = row["date"]
        if isinstance(row_date, pd.Timestamp):
            row_date = row_date.date()

        if row_date in existing_dates:
            # Track cumulative_pnl even for skipped rows so daily_pnl computation
            # uses the correct previous value
            prev_cumulative_pnl = row["account_value"] - row["cum_ledger"]
            continue

        account_value = row["account_value"]
        cum_ledger = row["cum_ledger"]
        cumulative_pnl = account_value - cum_ledger

        if prev_cumulative_pnl is not None:
            daily_pnl = cumulative_pnl - prev_cumulative_pnl
        else:
            daily_pnl = cumulative_pnl

        prev_cumulative_pnl = cumulative_pnl

        # Row format: 20 elements matching upsert_daily_prices with data_source
        # (vault_address, date, share_price, tvl, cumulative_pnl, cumulative_volume,
        #  daily_pnl, daily_return, follower_count, apr, is_closed, allow_deposits,
        #  leader_fraction, leader_commission, dep_count, wd_count,
        #  dep_usd, wd_usd, epoch_reset, data_source)
        rows_to_insert.append(
            (
                vault_address,
                row_date,
                1.0,  # placeholder share_price — will be recomputed
                account_value,  # tvl
                cumulative_pnl,
                row["cum_vlm"],
                daily_pnl,
                0.0,  # daily_return placeholder
                None,  # follower_count
                None,  # apr
                None,  # is_closed
                None,  # allow_deposits
                None,  # leader_fraction
                None,  # leader_commission
                None,  # daily_deposit_count
                None,  # daily_withdrawal_count
                None,  # daily_deposit_usd
                None,  # daily_withdrawal_usd
                None,  # epoch_reset
                "s3_backfill",  # data_source
            )
        )

    dates_inserted = len(rows_to_insert)
    dates_skipped = dates_found - dates_inserted

    if rows_to_insert:
        metrics_db.upsert_daily_prices(rows_to_insert)
        metrics_db.save()

        # Recompute share prices across full history (including new + existing rows)
        metrics_db.recompute_vault_share_prices(vault_address)
        metrics_db.save()

    return {
        "dates_found": dates_found,
        "dates_inserted": dates_inserted,
        "dates_skipped": dates_skipped,
    }


def apply_backfill(
    staging_db: HyperliquidS3StagingDatabase,
    metrics_db: HyperliquidDailyMetricsDatabase,
    vault_addresses: list[str] | None = None,
    overwrite_existing: bool = False,
) -> dict:
    """Stage 2: Apply staged vault data to main metrics database.

    For each vault in the staging database (or the provided list),
    inserts missing dates and recomputes share prices.

    :param staging_db:
        Staging database with extracted S3 data.
    :param metrics_db:
        Main daily metrics database.
    :param vault_addresses:
        Optional list of vault addresses to backfill. If None, processes
        all vaults in the staging database.
    :param overwrite_existing:
        If True, overwrite existing rows. If False, skip existing dates.
    :return:
        Summary dict with ``vaults_processed``, ``total_inserted``, ``total_skipped``.
    """
    if vault_addresses is None:
        vault_addresses = staging_db.get_all_vault_addresses()
    else:
        vault_addresses = [a.lower() for a in vault_addresses]

    total_inserted = 0
    total_skipped = 0
    vaults_with_data = 0

    progress = tqdm(
        vault_addresses,
        desc="Backfilling vaults",
        unit="vault",
    )

    for vault_address in progress:
        result = apply_backfill_single_vault(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_address=vault_address,
            overwrite_existing=overwrite_existing,
        )
        total_inserted += result["dates_inserted"]
        total_skipped += result["dates_skipped"]
        if result["dates_inserted"] > 0:
            vaults_with_data += 1

        progress.set_postfix(
            inserted=total_inserted,
            skipped=total_skipped,
            filled=vaults_with_data,
        )

    return {
        "vaults_processed": len(vault_addresses),
        "vaults_with_new_data": vaults_with_data,
        "total_inserted": total_inserted,
        "total_skipped": total_skipped,
    }
