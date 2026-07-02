"""Export vault database files (parquet, pickle, DuckDB) to Cloudflare R2.

Uploads price databases, metadata pickles, Core3 risk intelligence
DuckDB, and exchange-rate DuckDB so the backtester can download them
without running the full scan pipeline.

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
    - CORE3_DATABASE_PATH: Path to Core3 DuckDB export (default: ~/.tradingstrategy/vaults/core3/core3.duckdb)
    - CURRENCY_API_DB_PATH / CURRENCY_API_DATABASE_PATH: Path to exchange-rate DuckDB export
      (default: $PIPELINE_DATA_DIR/exchange-rates.duckdb)
    - UPLOAD_PREFIX: Prefix for S3 keys, e.g. "test-" (default: "")
"""

from eth_defi.vault.data_file_export import main

if __name__ == "__main__":
    main()
