"""Export vault database files to Cloudflare R2.

Uploads price databases, metadata pickles, Core3 risk intelligence and
exchange-rate files to the private vault-data bucket. Public R2 uploads are
limited to explicitly generated sample files.
"""

import logging
import os
from pathlib import Path

import duckdb
import pyarrow as pa
from tqdm_loggable.auto import tqdm

from eth_defi.cloudflare_r2 import copy_r2_object_daily_backup, create_r2_client, upload_file_to_r2
from eth_defi.core3.constants import resolve_core3_database_path
from eth_defi.currency_api.constants import CURRENCY_API_DATABASE
from eth_defi.currency_api.parquet import materialise_exchange_rate_parquet
from eth_defi.utils import setup_console_logging
from eth_defi.vault.settlement_data import (
    VAULT_SETTLEMENT_DATABASE_FILENAME,
    checkpoint_vault_settlement_database_if_exists,
)
from eth_defi.vault.vaultdb import get_pipeline_data_dir
from eth_defi.xerberus.constants import resolve_xerberus_database_path

logger = logging.getLogger(__name__)

EXCHANGE_RATE_DATABASE_FILENAME = "exchange-rates.duckdb"
EXCHANGE_RATE_PARQUET_FILENAME = "exchange-rates.parquet"


def resolve_exchange_rate_database_path(base_path: Path | None = None) -> Path:
    """Resolve the exchange-rate DuckDB database path.

    The exchange-rate bundle is produced by the currency API scanner. Use
    the same ``CURRENCY_API_DB_PATH`` / ``CURRENCY_API_DATABASE_PATH``
    override contract as the all-chain scanner so exports upload the
    database that the scanner writes. Without an override, data exports use
    ``exchange-rates.duckdb`` under the active pipeline data directory.

    :param base_path:
        Pipeline data directory. ``None`` falls back to the standalone
        currency API default path.
    :return:
        Path to the exchange-rate DuckDB database.
    """
    path = os.environ.get("CURRENCY_API_DB_PATH") or os.environ.get("CURRENCY_API_DATABASE_PATH")
    if path:
        return Path(path).expanduser()
    if base_path is not None:
        return base_path / EXCHANGE_RATE_DATABASE_FILENAME
    return CURRENCY_API_DATABASE


def resolve_exchange_rate_parquet_path(base_path: Path) -> Path:
    """Resolve the cleaned exchange-rate Parquet destination.

    The currency API DuckDB database may be kept outside pipeline state through
    ``CURRENCY_API_DB_PATH``. The flat exported Parquet always belongs below the
    selected pipeline data directory so its R2 key stays stable.

    :param base_path:
        Active pipeline data directory.
    :return:
        Destination path for ``exchange-rates.parquet``.
    """
    return base_path / EXCHANGE_RATE_PARQUET_FILENAME


def get_data_file_paths(
    base_path: Path,
    core3_db_path: Path | None = None,
    exchange_rate_db_path: Path | None = None,
    xerberus_db_path: Path | None = None,
    *,
    exchange_rate_parquet_path: Path | None = None,
    include_exchange_rate_parquet: bool = True,
) -> list[Path]:
    """Build the data file list uploaded to R2.

    :param base_path:
        Pipeline data directory.
    :param core3_db_path:
        Optional Core3 DuckDB path override.
    :param exchange_rate_db_path:
        Optional exchange-rate DuckDB path override.
    :param xerberus_db_path:
        Optional Xerberus DuckDB path override.
    :param exchange_rate_parquet_path:
        Optional already materialised cleaned exchange-rate Parquet path.
    :param include_exchange_rate_parquet:
        Include the cleaned Parquet when its materialisation succeeded for this
        run. Set false to avoid re-uploading a stale prior file after a failure.
    :return:
        Files to upload, including optional files that may be skipped later
        if they do not exist.
    """
    sticky_export_state_paths = [base_path / "vault-export-state.json"]
    exchange_rate_path = exchange_rate_db_path or resolve_exchange_rate_database_path(base_path)
    paths = [
        base_path / "vault-prices-1h.parquet",
        base_path / "cleaned-vault-prices-1h.parquet",
        base_path / "vault-metadata-db.pickle",
        base_path / "vault-reader-state-1h.pickle",
        base_path / VAULT_SETTLEMENT_DATABASE_FILENAME,
        core3_db_path or resolve_core3_database_path(),
        xerberus_db_path or resolve_xerberus_database_path(),
        exchange_rate_path,
        *sticky_export_state_paths,
    ]
    if include_exchange_rate_parquet:
        paths.append(exchange_rate_parquet_path or resolve_exchange_rate_parquet_path(base_path))
    return paths


def upload_files_to_r2(  # noqa: PLR0917
    file_paths: list[Path],
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    key_prefix: str = "",
) -> int:
    """Upload a list of files to R2 bucket, excluding tmp* files.

    :param file_paths:
        List of file paths to upload.
    :param bucket_name:
        R2 bucket name.
    :param endpoint_url:
        R2 API endpoint URL.
    :param access_key_id:
        R2 access key ID.
    :param secret_access_key:
        R2 secret access key.
    :param key_prefix:
        Prefix for S3 keys, e.g. ``test-`` for test uploads.
    :return:
        Number of files uploaded.
    """
    files_to_upload = []
    for file_path in file_paths:
        if file_path.name.startswith("tmp"):
            continue
        if not file_path.exists():
            logger.warning("File does not exist, skipping: %s", file_path)
            continue
        files_to_upload.append(file_path)

    if not files_to_upload:
        logger.info("No files to upload after filtering")
        return 0

    logger.info(
        "Checking %d files for R2 upload to bucket %s (excluded %d files)",
        len(files_to_upload),
        bucket_name,
        len(file_paths) - len(files_to_upload),
    )

    s3_client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    uploaded_count = 0
    skipped_count = 0

    for file_path in files_to_upload:
        # Data file exports intentionally use flat object keys so Core3 sits
        # next to the vault parquet/pickle files consumed by downstream jobs.
        s3_key = f"{key_prefix}{file_path.name}"
        file_size = file_path.stat().st_size

        with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Uploading {s3_key}") as progress_bar:

            def upload_callback(bytes_amount: int) -> None:
                progress_bar.update(bytes_amount)

            uploaded = upload_file_to_r2(
                s3_client=s3_client,
                file_path=file_path,
                bucket_name=bucket_name,
                object_name=s3_key,
                skip_if_current=True,
                callback=upload_callback,
            )

        if uploaded:
            uploaded_count += 1
            logger.info("Uploaded %s to s3://%s/%s", file_path, bucket_name, s3_key)
        else:
            skipped_count += 1
            logger.info("Skipped unchanged file %s for s3://%s/%s", file_path, bucket_name, s3_key)

    logger.info(
        "Data file upload summary for bucket %s: %d uploaded, %d skipped unchanged",
        bucket_name,
        uploaded_count,
        skipped_count,
    )

    return uploaded_count


def publish_exchange_rate_parquet_to_alternative_bucket(parquet_path: Path) -> None:
    """Publish one verified exchange-rate Parquet to the private data bucket.

    The focused crypto exporter uses this before its metadata bundle is made
    current. It shares the flat key, upload prefix and daily-backup convention
    with the complete data-file export without uploading unrelated vault files.

    :param parquet_path:
        Existing verified ``exchange-rates.parquet`` snapshot.
    :return:
        ``None`` after upload and optional daily backup.
    :raises FileNotFoundError:
        If the supplied local snapshot does not exist.
    :raises AssertionError:
        If private R2 configuration is incomplete.
    """
    if not parquet_path.is_file():
        raise FileNotFoundError(parquet_path)

    bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    assert bucket_name, "R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME is required"
    assert access_key_id, "R2 data access key is required"
    assert secret_access_key, "R2 data secret key is required"
    assert endpoint_url, "R2 data endpoint URL is required"

    client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    object_key = f"{os.environ.get('UPLOAD_PREFIX', '')}{parquet_path.name}"
    upload_file_to_r2(
        s3_client=client,
        file_path=parquet_path,
        bucket_name=bucket_name,
        object_name=object_key,
        skip_if_current=True,
    )
    if os.environ.get("R2_DAILY_BACKUP", "true").lower() != "false":
        copy_r2_object_daily_backup(client, bucket_name, object_key)


def main(
    exchange_rate_parquet_path: Path | None = None,
    exchange_rate_parquet_error: Exception | None = None,
) -> None:
    """Run the data file export script.

    Reads R2 configuration from environment variables, uploads vault data
    files to the private bucket, and creates daily backups there when enabled.
    A scheduled caller can supply the exact Parquet
    snapshot already used for crypto USD metrics. Standalone use materialises a
    fresh snapshot before upload.

    :param exchange_rate_parquet_path:
        Optional verified Parquet snapshot created earlier in this scan cycle.
    :param exchange_rate_parquet_error:
        Earlier materialisation failure. Unrelated files still upload, after
        which this error is propagated and stale Parquet is omitted.
    """
    setup_console_logging(
        log_file=Path("logs/export-data-files.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    # Data-file exports deliberately target only the private bucket. Public
    # downloads are provided by the separate sample-file export.
    bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    upload_prefix = os.environ.get("UPLOAD_PREFIX", "")

    assert bucket_name, "R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME environment variable is required"

    base_path = get_pipeline_data_dir()
    if exchange_rate_parquet_path is None and exchange_rate_parquet_error is None:
        try:
            exchange_rate_parquet_path = materialise_exchange_rate_parquet(
                source_path=resolve_exchange_rate_database_path(base_path),
                destination_path=resolve_exchange_rate_parquet_path(base_path),
            ).path
        except (duckdb.Error, FileNotFoundError, KeyError, OSError, TypeError, ValueError, pa.ArrowException) as exc:
            logger.exception("Exchange-rate Parquet materialisation failed")
            exchange_rate_parquet_error = exc

    paths = get_data_file_paths(
        base_path,
        exchange_rate_parquet_path=exchange_rate_parquet_path,
        include_exchange_rate_parquet=exchange_rate_parquet_error is None,
    )
    checkpoint_vault_settlement_database_if_exists(base_path / VAULT_SETTLEMENT_DATABASE_FILENAME)

    logger.info("Exporting %d data files to private R2 bucket %s via %s", len(paths), bucket_name, endpoint_url)
    logger.info("Data-file key prefix: %s", upload_prefix or "(none)")
    for path in paths:
        exists = path.exists()
        size = f"{path.stat().st_size / 1024 / 1024:.1f} MB" if exists else "MISSING"
        logger.info("Data file: %s (%s)", path.name, size)

    upload_files_to_r2(
        file_paths=paths,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        key_prefix=upload_prefix,
    )

    if os.environ.get("R2_DAILY_BACKUP", "true").lower() != "false":
        s3_client = create_r2_client(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        backup_created = 0
        backup_skipped = 0
        for file_path in paths:
            if not file_path.exists():
                continue
            source_key = f"{upload_prefix}{file_path.name}"
            if copy_r2_object_daily_backup(s3_client, bucket_name, source_key):
                backup_created += 1
            else:
                backup_skipped += 1
        logger.info(
            "Daily backup summary for private bucket %s: %d created, %d skipped",
            bucket_name,
            backup_created,
            backup_skipped,
        )

    if exchange_rate_parquet_error is not None:
        raise exchange_rate_parquet_error
