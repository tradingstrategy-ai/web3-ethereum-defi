"""Build and publish only the private crypto-vaults bundle.

This command runs the same isolated daily cleaner, metadata calculation and
private R2 publisher as scheduled all-chain post-processing. It does not run
native protocol merges or touch any legacy public export.

Environment variables:

- ``PIPELINE_DATA_DIR``: Common vault data directory.
- ``VAULT_DATABASE``: Vault metadata pickle. Defaults below the pipeline data
  directory.
- ``UNCLEANED_PRICE_DATABASE``: Shared raw price Parquet. Defaults below the
  pipeline data directory.
- ``CLEANED_STABLECOIN_PRICE_DATABASE``: Existing standard stablecoin-only
  cleaned Parquet. Defaults below the pipeline data directory.
- ``CRYPTO_VAULTS_DIRECTORY``: Local private bundle directory. Defaults to
  ``crypto-vaults`` below the pipeline data directory.
- ``SETTLEMENT_DATABASE``: Optional vault settlement DuckDB database.
- ``CRYPTO_VAULTS_MIN_TVL_USD``: Fixed USD low-TVL guideline.
- ``CRYPTO_VAULTS_PUBLISH``: Set to ``false`` to build local artefacts without
  uploading them to R2. Defaults to ``true``.
- ``UPLOAD_PREFIX``: Optional private R2 key prefix, such as ``test-``.

Private R2 configuration uses ``R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME``
and either the ``R2_DATA_*`` or ``R2_VAULT_METADATA_*`` credential set.
"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.crypto_vault_export import publish_crypto_vault_bundle
from eth_defi.vault.crypto_vaults import (
    build_crypto_vault_metadata,
    build_crypto_vault_prices,
    resolve_crypto_vault_paths,
)
from eth_defi.vault.settlement_data import get_default_vault_settlement_database_path
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def _resolve_path(value: str | None, default: Path) -> Path:
    """Resolve one environment path while preserving the established default.

    :param value:
        Optional environment value.
    :param default:
        Default filesystem path.
    :return:
        Expanded explicit path.
    """
    return Path(value).expanduser() if value else default


def main() -> None:
    """Build metadata and optionally publish the private crypto-vaults bundle.

    The command intentionally lets errors propagate: unlike the scheduled
    pipeline, an operator running this focused repair command needs a non-zero
    exit status when any of its three required phases fails.

    :return:
        ``None`` after a successful private export.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    data_dir = get_pipeline_data_dir()
    vault_db_path = _resolve_path(os.environ.get("VAULT_DATABASE"), data_dir / "vault-metadata-db.pickle")
    uncleaned_path = _resolve_path(os.environ.get("UNCLEANED_PRICE_DATABASE"), data_dir / "vault-prices-1h.parquet")
    cleaned_stablecoin_path = _resolve_path(os.environ.get("CLEANED_STABLECOIN_PRICE_DATABASE"), data_dir / "cleaned-vault-prices-1h.parquet")
    crypto_directory = os.environ.get("CRYPTO_VAULTS_DIRECTORY")
    crypto_paths = resolve_crypto_vault_paths(data_dir, Path(crypto_directory).expanduser() if crypto_directory else None)
    settlement_db_path = _resolve_path(os.environ.get("SETTLEMENT_DATABASE"), get_default_vault_settlement_database_path())

    for path in (vault_db_path, uncleaned_path, cleaned_stablecoin_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not settlement_db_path.exists():
        logger.info("Settlement database is absent; continuing without it: %s", settlement_db_path)
        settlement_db_path = None

    logger.info("Building private crypto-vaults bundle in %s", crypto_paths.directory)
    build_crypto_vault_prices(
        vault_db_path=vault_db_path,
        uncleaned_path=uncleaned_path,
        cleaned_path=crypto_paths.cleaned_price_path,
        cleaned_stablecoin_path=cleaned_stablecoin_path,
        settlement_db_path=settlement_db_path,
    )
    metadata = build_crypto_vault_metadata(
        vault_db_path=vault_db_path,
        cleaned_price_path=crypto_paths.cleaned_price_path,
        metadata_path=crypto_paths.metadata_path,
        sticky_state_path=crypto_paths.sticky_state_path,
    )
    publish = os.environ.get("CRYPTO_VAULTS_PUBLISH", "true").lower() != "false"
    if publish:
        publish_crypto_vault_bundle(crypto_paths, metadata)
        logger.info("Published private crypto-vaults bundle with %d vaults", len(metadata["vaults"]))
    else:
        logger.info("Built local crypto-vaults artefacts without R2 publication")

    counts = {family: sum(vault["denomination_family"] == family for vault in metadata["vaults"]) for family in metadata["denomination_families"]}
    print(tabulate([counts], headers="keys", tablefmt="rounded_outline"))


if __name__ == "__main__":
    main()
