"""Export vault protocol metadata and logos to Cloudflare R2.

Reads all vault protocol metadata YAML files, converts them to JSON
with logo URLs, and uploads metadata JSON and formatted logo files to R2.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-protocol-metadata.py

Environment variables:
    - R2_VAULT_METADATA_BUCKET_NAME: R2 bucket name (required)
    - R2_VAULT_METADATA_ACCESS_KEY_ID: R2 access key ID (required)
    - R2_VAULT_METADATA_SECRET_ACCESS_KEY: R2 secret access key (required)
    - R2_VAULT_METADATA_ENDPOINT_URL: R2 API endpoint URL (required)
    - R2_VAULT_METADATA_PUBLIC_URL: Public base URL for logo URLs in metadata (required)
    - MAX_WORKERS: Number of parallel upload workers (default: 20)
"""

import logging
import os
from pathlib import Path

from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.utils import setup_console_logging
from eth_defi.vault.protocol_metadata import (
    METADATA_DIR,
    get_available_logos,
    process_and_upload_protocol_metadata,
)


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

    yaml_files = list(METADATA_DIR.glob("*.yaml"))
    slugs = [f.stem for f in yaml_files]
    logger.info("Found %d protocol metadata files", len(slugs))

    def _process_slug(slug: str):
        process_and_upload_protocol_metadata(
            slug=slug,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_url=public_url,
        )

    tasks = (delayed(_process_slug)(slug) for slug in slugs)
    Parallel(n_jobs=max_workers, prefer="threads")(tqdm(tasks, total=len(slugs), desc="Uploading protocol metadata"))

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


if __name__ == "__main__":
    main()
