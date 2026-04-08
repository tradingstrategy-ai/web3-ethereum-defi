"""Export vault database files (parquet, pickle) to Cloudflare R2.

Uploads price databases and metadata pickles so the backtester
can download them without running the full scan pipeline.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-data-files.py

Environment variables:
    - R2_DATA_BUCKET_NAME: R2 bucket for data files (falls back to R2_VAULT_METADATA_BUCKET_NAME)
    - R2_DATA_ACCESS_KEY_ID: R2 access key ID (falls back to R2_VAULT_METADATA_ACCESS_KEY_ID)
    - R2_DATA_SECRET_ACCESS_KEY: R2 secret access key (falls back to R2_VAULT_METADATA_SECRET_ACCESS_KEY)
    - R2_DATA_ENDPOINT_URL: R2 endpoint URL (falls back to R2_VAULT_METADATA_ENDPOINT_URL)
    - R2_DATA_PUBLIC_URL: Public base URL for data files (falls back to R2_VAULT_METADATA_PUBLIC_URL)
    - R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME: Alternative R2 bucket for the upcoming private
      commercial professional vault data bucket (optional, uses same credentials as primary)
    - UPLOAD_PREFIX: Prefix for S3 keys, e.g. "test-" (default: "")
"""

import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from tqdm_loggable.auto import tqdm

from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def upload_files_to_r2(
    file_paths: list[Path],
    bucket_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    key_prefix: str = "",
    public_url: str = "",
) -> int:
    """Upload a list of files to R2 bucket, excluding tmp* files.

    :param file_paths: List of file paths to upload
    :param bucket_name: R2 bucket name
    :param endpoint_url: R2 API endpoint URL
    :param access_key_id: R2 access key ID
    :param secret_access_key: R2 secret access key
    :param key_prefix: Prefix for S3 keys (e.g. ``test-`` for test uploads)
    :param public_url: Public base URL for logging final download URLs
    :return: Number of files uploaded
    """
    # Filter out tmp* files and files that don't exist
    files_to_upload = []
    for f in file_paths:
        if f.name.startswith("tmp"):
            continue
        if not f.exists():
            logger.warning("File does not exist, skipping: %s", f)
            continue
        files_to_upload.append(f)

    if not files_to_upload:
        logger.info("No files to upload after filtering")
        return 0

    logger.info("Uploading %d files to R2 bucket %s (excluded %d files)", len(files_to_upload), bucket_name, len(file_paths) - len(files_to_upload))

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )

    for file_path in files_to_upload:
        s3_key = f"{key_prefix}{file_path.name}"
        file_size = file_path.stat().st_size

        logger.info("Uploading %s to s3://%s/%s", file_path, bucket_name, s3_key)

        with open(file_path, "rb") as f:
            with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Uploading {s3_key}") as pbar:

                def upload_callback(bytes_amount):
                    pbar.update(bytes_amount)

                try:
                    s3_client.upload_fileobj(
                        f,
                        bucket_name,
                        s3_key,
                        Callback=upload_callback,
                    )
                except ClientError as e:
                    raise RuntimeError(f"Failed to upload {s3_key} to bucket {bucket_name} (endpoint: {endpoint_url}, access_key_id: {access_key_id}): {e}") from e

        if public_url:
            final_url = f"{public_url.rstrip('/')}/{s3_key}"
            print(f"  -> {final_url}")

    return len(files_to_upload)


def main():
    setup_console_logging(
        log_file=Path("logs/export-data-files.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    # Data files use R2_DATA_* env vars, falling back to R2_VAULT_METADATA_*
    bucket_name = os.environ.get("R2_DATA_BUCKET_NAME") or os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_DATA_ACCESS_KEY_ID") or os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_DATA_SECRET_ACCESS_KEY") or os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_DATA_ENDPOINT_URL") or os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_DATA_PUBLIC_URL") or os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")
    upload_prefix = os.environ.get("UPLOAD_PREFIX", "")

    assert bucket_name, "R2_DATA_BUCKET_NAME (or R2_VAULT_METADATA_BUCKET_NAME) environment variable is required"

    # Build list of target buckets.
    # The alternative bucket is for the upcoming private commercial professional vault data bucket.
    # When set, data files are uploaded to both the primary and alternative buckets
    # using the same credentials.
    bucket_names = [bucket_name]
    alt_bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    if alt_bucket_name:
        bucket_names.append(alt_bucket_name)
        logger.info("Alternative bucket configured: %s", alt_bucket_name)

    base_path = get_pipeline_data_dir()
    paths = [
        base_path / "vault-prices-1h.parquet",
        base_path / "cleaned-vault-prices-1h.parquet",
        base_path / "vault-metadata-db.pickle",
        base_path / "vault-reader-state-1h.pickle",
    ]

    print(f"\nExporting data files to R2")
    print(f"  Bucket: {bucket_name}")
    if alt_bucket_name:
        print(f"  Alternative bucket: {alt_bucket_name}")
    print(f"  Endpoint: {endpoint_url}")
    print(f"  Public URL: {public_url}")
    print(f"  Key prefix: '{upload_prefix}'" if upload_prefix else "  Key prefix: (none)")
    print(f"  Files: {len(paths)}")
    for p in paths:
        exists = p.exists()
        size = f"{p.stat().st_size / 1024 / 1024:.1f} MB" if exists else "MISSING"
        print(f"    - {p.name}: {size}")

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


if __name__ == "__main__":
    main()
