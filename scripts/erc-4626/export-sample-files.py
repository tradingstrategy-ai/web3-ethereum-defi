"""Export Ethereum-only sample vault data files to Cloudflare R2.

Thin CLI wrapper around :py:func:`eth_defi.vault.sample_export.export_sample_files_to_r2`.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/export-sample-files.py

Environment variables:
    - R2_DATA_BUCKET_NAME: R2 bucket for data files (falls back to R2_VAULT_METADATA_BUCKET_NAME)
    - R2_DATA_ACCESS_KEY_ID: R2 access key ID (falls back to R2_VAULT_METADATA_ACCESS_KEY_ID)
    - R2_DATA_SECRET_ACCESS_KEY: R2 secret access key (falls back to R2_VAULT_METADATA_SECRET_ACCESS_KEY)
    - R2_DATA_ENDPOINT_URL: R2 endpoint URL (falls back to R2_VAULT_METADATA_ENDPOINT_URL)
    - R2_DATA_PUBLIC_URL: Public base URL for data files (falls back to R2_VAULT_METADATA_PUBLIC_URL)
    - UPLOAD_PREFIX: Prefix for S3 keys, e.g. "test-" (default: "")
    - PIPELINE_DATA_DIR: Override pipeline data directory (default: ~/.tradingstrategy/vaults)
"""

from pathlib import Path

from eth_defi.utils import setup_console_logging
from eth_defi.vault.sample_export import export_sample_files_to_r2


def main():
    setup_console_logging(
        log_file=Path("logs/export-sample-files.log"),
        only_log_file=False,
        clear_log_file=False,
    )
    export_sample_files_to_r2()


if __name__ == "__main__":
    main()
