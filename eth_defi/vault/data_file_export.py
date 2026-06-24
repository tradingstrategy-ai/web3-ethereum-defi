"""Export vault database files to Cloudflare R2.

Uploads price databases, metadata pickles, and Core3 risk intelligence
DuckDB so the backtester can download them without running the full scan
pipeline.
"""

import logging
import os
from pathlib import Path

from tqdm_loggable.auto import tqdm

from eth_defi.cloudflare_r2 import copy_r2_object_daily_backup, create_r2_client, upload_file_to_r2
from eth_defi.core3.constants import resolve_core3_database_path
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def get_data_file_paths(base_path: Path, core3_db_path: Path | None = None) -> list[Path]:
    """Build the data file list uploaded to R2.

    :param base_path:
        Pipeline data directory.
    :param core3_db_path:
        Optional Core3 DuckDB path override.
    :return:
        Files to upload, including optional files that may be skipped later
        if they do not exist.
    """
    sticky_export_state_paths = [base_path / "vault-export-state.json"]
    return [
        base_path / "vault-prices-1h.parquet",
        base_path / "cleaned-vault-prices-1h.parquet",
        base_path / "vault-metadata-db.pickle",
        base_path / "vault-reader-state-1h.pickle",
        core3_db_path or resolve_core3_database_path(),
        *sticky_export_state_paths,
    ]


def upload_files_to_r2(  # noqa: PLR0917
    file_paths: list[Path],
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    key_prefix: str = "",
    public_url: str = "",
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
    :param public_url:
        Public base URL for logging final download URLs.
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

        if public_url and uploaded:
            final_url = f"{public_url.rstrip('/')}/{s3_key}"
            print(f"  -> {final_url}")

    logger.info(
        "Data file upload summary for bucket %s: %d uploaded, %d skipped unchanged",
        bucket_name,
        uploaded_count,
        skipped_count,
    )

    return uploaded_count


def main() -> None:
    """Run the data file export script.

    Reads R2 configuration from environment variables, uploads vault data
    files to configured buckets, and creates daily backups in the
    alternative bucket when enabled.
    """
    setup_console_logging(
        log_file=Path("logs/export-data-files.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    # Data files use R2_DATA_* env vars, falling back to R2_VAULT_METADATA_*.
    bucket_name = os.environ.get("R2_DATA_BUCKET_NAME") or os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_DATA_PUBLIC_URL") or os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")
    upload_prefix = os.environ.get("UPLOAD_PREFIX", "")

    assert bucket_name, "R2_DATA_BUCKET_NAME (or R2_VAULT_METADATA_BUCKET_NAME) environment variable is required"

    # The alternative bucket is for the upcoming private commercial
    # professional vault data bucket. When set, data files are uploaded to
    # both the primary and alternative buckets using the same credentials.
    bucket_names = [bucket_name]
    alt_bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    if alt_bucket_name:
        bucket_names.append(alt_bucket_name)
        logger.info("Alternative bucket configured: %s", alt_bucket_name)

    base_path = get_pipeline_data_dir()
    paths = get_data_file_paths(base_path)

    print("\nExporting data files to R2")
    print(f"  Bucket: {bucket_name}")
    if alt_bucket_name:
        print(f"  Alternative bucket: {alt_bucket_name}")
    print(f"  Endpoint: {endpoint_url}")
    print(f"  Public URL: {public_url}")
    print(f"  Key prefix: '{upload_prefix}'" if upload_prefix else "  Key prefix: (none)")
    print(f"  Files: {len(paths)}")
    for path in paths:
        exists = path.exists()
        size = f"{path.stat().st_size / 1024 / 1024:.1f} MB" if exists else "MISSING"
        print(f"    - {path.name}: {size}")

    daily_backup_enabled = os.environ.get("R2_DAILY_BACKUP", "true").lower() != "false"

    for current_bucket in bucket_names:
        if len(bucket_names) > 1:
            logger.info("Uploading data files to bucket: %s", current_bucket)

        upload_files_to_r2(
            file_paths=paths,
            bucket_name=current_bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            key_prefix=upload_prefix,
            public_url=public_url,
        )

        # Create daily timestamped backups in the alternative (private) bucket only.
        if current_bucket == alt_bucket_name and daily_backup_enabled:
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
                if copy_r2_object_daily_backup(s3_client, current_bucket, source_key):
                    backup_created += 1
                else:
                    backup_skipped += 1
            logger.info(
                "Daily backup summary for bucket %s: %d created, %d skipped",
                current_bucket,
                backup_created,
                backup_skipped,
            )
