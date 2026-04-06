"""Export vault protocol metadata and logos to Cloudflare R2.

Reads all vault protocol metadata YAML files, converts them to JSON
with logo URLs, and uploads metadata JSON and formatted logo files to R2.
Also uploads stablecoin and curator metadata with aggregate indices.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-protocol-metadata.py

Environment variables:
    - R2_VAULT_METADATA_BUCKET_NAME: R2 bucket for protocol/stablecoin metadata and logos (required)
    - R2_VAULT_METADATA_ACCESS_KEY_ID: R2 access key ID for metadata bucket (required)
    - R2_VAULT_METADATA_SECRET_ACCESS_KEY: R2 secret access key for metadata bucket (required)
    - R2_VAULT_METADATA_ENDPOINT_URL: R2 API endpoint URL for metadata bucket (required)
    - R2_VAULT_METADATA_PUBLIC_URL: Public base URL for logo URLs in metadata (required)
    - R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME: Alternative R2 bucket for the upcoming private
      commercial professional vault data bucket (optional, uses same credentials as primary)
    - MAX_WORKERS: Number of parallel upload workers (default: 20)
"""

import logging
import os
from pathlib import Path

from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR, process_and_upload_stablecoin_metadata, upload_stablecoin_index
from eth_defi.utils import setup_console_logging
from eth_defi.vault.protocol_metadata import METADATA_DIR, get_available_logos, process_and_upload_protocol_metadata

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        log_file=Path("logs/export-protocol-metadata.log"),
        only_log_file=False,
        clear_log_file=False,
    )

    bucket_name = os.environ.get("R2_VAULT_METADATA_BUCKET_NAME")
    access_key_id = os.environ.get("R2_VAULT_METADATA_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_VAULT_METADATA_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_VAULT_METADATA_ENDPOINT_URL")
    public_url = os.environ.get("R2_VAULT_METADATA_PUBLIC_URL")
    max_workers = int(os.environ.get("MAX_WORKERS", "20"))

    assert bucket_name, "R2_VAULT_METADATA_BUCKET_NAME environment variable is required"
    assert public_url, "R2_VAULT_METADATA_PUBLIC_URL environment variable is required"

    # Build list of target buckets.
    # The alternative bucket is for the upcoming private commercial professional vault data bucket.
    # When set, all uploads go to both the primary and alternative buckets using the same credentials.
    bucket_names = [bucket_name]
    alt_bucket_name = os.environ.get("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    if alt_bucket_name:
        bucket_names.append(alt_bucket_name)
        logger.info("Alternative bucket configured: %s", alt_bucket_name)

    yaml_files = list(METADATA_DIR.glob("*.yaml"))
    slugs = [f.stem for f in yaml_files]
    logger.info("Found %d protocol metadata files", len(slugs))

    stablecoin_files = list(STABLECOINS_DATA_DIR.glob("*.yaml"))
    logger.info("Found %d stablecoin metadata files", len(stablecoin_files))

    for current_bucket in bucket_names:
        if len(bucket_names) > 1:
            logger.info("Uploading to bucket: %s", current_bucket)

        # Upload protocol metadata
        def _process_slug(slug: str, _bucket: str = current_bucket):
            process_and_upload_protocol_metadata(
                slug=slug,
                bucket_name=_bucket,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_url=public_url,
            )

        tasks = (delayed(_process_slug)(slug) for slug in slugs)
        Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(slugs), desc="Uploading protocol metadata"))

        # Upload stablecoin metadata
        def _process_stablecoin(yaml_path: Path, _bucket: str = current_bucket):
            process_and_upload_stablecoin_metadata(
                yaml_path=yaml_path,
                bucket_name=_bucket,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_url=public_url,
            )

        stablecoin_tasks = (delayed(_process_stablecoin)(f) for f in stablecoin_files)
        Parallel(n_jobs=max_workers, prefer="threads")(tqdm(stablecoin_tasks, total=len(stablecoin_files), desc="Uploading stablecoin metadata"))

        # Upload aggregate stablecoin index
        index = upload_stablecoin_index(
            bucket_name=current_bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_url=public_url,
        )

    # Build summary table of exported protocols and logo availability
    table_data = []
    for slug in sorted(slugs):
        logos = get_available_logos(slug)
        table_data.append(
            [
                slug,
                "Yes" if logos["light"] else "No",
                "Yes" if logos["dark"] else "No",
            ]
        )

    print("\nProtocol metadata export complete\n")
    print(
        tabulate(
            table_data,
            headers=["Protocol", "Light logo", "Dark logo"],
            tablefmt="simple",
        )
    )

    print(f"\nStablecoin metadata export complete: {len(stablecoin_files)} stablecoins, {len(index)} index entries\n")


if __name__ == "__main__":
    main()
