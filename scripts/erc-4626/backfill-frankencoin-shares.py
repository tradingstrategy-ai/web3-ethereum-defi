"""Reclassify and optionally backfill only Ethereum Frankencoin Shares.

``DRY_RUN=true`` is the default and writes reviewable metadata and optional
price copies to a retained temporary directory. Applied runs hold the scanner
lock, back up metadata, and publish the staged Parquet only after a successful
non-empty scan. Existing reader state and chain discovery cursors are preserved.

Environment variables:

- ``JSON_RPC_ETHEREUM`` and ``HYPERSYNC_API_KEY``: configured providers.
- ``DRY_RUN``: defaults to ``true``; set ``false`` to publish outputs.
- ``FRANKENCOIN_SHARES_SCAN_PRICES``: defaults to ``false`` for metadata only.
- ``VAULT_DB_PATH`` and ``PARQUET_PATH``: shared metadata and raw price paths.
- ``TIMESTAMP_CACHE``: preserved dense timestamp cache directory.
- ``START_BLOCK`` and ``END_BLOCK``: optional inclusive/exclusive price bounds;
  defaults are FCS deployment and the safe chain head.
- ``MAX_WORKERS``: archive-reading workers, default ``4``.
- ``LOG_LEVEL``: logging level, default ``info``.

Use the protocol documentation for production Compose commands. Historical
event reads use Hypersync, never JSON-RPC event queries.
"""

import logging
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path

from atomicwrites import atomic_write
from tabulate import tabulate

from eth_defi.erc_4626.vault_protocol.frankencoin.backfill import apply_frankencoin_shares_metadata, fetch_frankencoin_shares_metadata, fetch_frankencoin_shares_prices
from eth_defi.erc_4626.vault_protocol.frankencoin.constants import FRANKENCOIN_SHARES_ADDRESS, FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import pformat_scan_result
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean setting without accidentally enabling persistent writes.

    Reject misspellings so dry-run and price-scan choices remain explicit.

    :param name: Environment variable name.
    :param default: Value when the variable is absent.
    :return: Parsed boolean setting.
    :raises ValueError: If the supplied setting is not a boolean literal.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


def main() -> None:  # noqa: PLR0914
    """Prepare and publish an address-scoped backfill from environment settings.

    All network reads and price validation finish before metadata publication.
    Dry runs retain their staged outputs for operator inspection.

    :return: ``None`` after reporting retained or published output paths.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    pipeline_dir = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    parquet_path = Path(os.environ.get("PARQUET_PATH", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    timestamp_cache_path = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    dry_run = parse_bool_env("DRY_RUN", default=True)
    scan_prices = parse_bool_env("FRANKENCOIN_SHARES_SCAN_PRICES", default=False)
    if not dry_run and not vault_db_path.is_file():
        raise FileNotFoundError(f"Applied FCS backfill requires an existing metadata database: {vault_db_path}")
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers < 1:
        msg = "MAX_WORKERS must be positive"
        raise ValueError(msg)
    rpc_url = read_json_rpc_url(1)
    web3 = create_multi_provider_web3(rpc_url)
    metadata_block = get_almost_latest_block_number(web3)
    start_block = int(os.environ.get("START_BLOCK", FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK))
    end_block = int(os.environ.get("END_BLOCK", metadata_block))
    if scan_prices and (start_block < FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK or not start_block < end_block <= metadata_block):
        msg = "Price range must start at or after FCS deployment and end no later than the safe head"
        raise ValueError(msg)

    # Applied price staging must share the destination filesystem for atomic
    # replacement. Dry runs use a retained directory containing all outputs.
    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        if scan_prices:
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = None if dry_run else (parquet_path.parent if scan_prices else vault_db_path.parent)
    stage_directory = Path(tempfile.mkdtemp(prefix="frankencoin-shares-backfill-", dir=stage_parent))
    logger.info("FCS backfill dry_run=%s, scan_prices=%s, staging=%s", dry_run, scan_prices, stage_directory)
    lock = nullcontext() if dry_run else wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60)
    token_cache = TokenDiskCache(stage_directory / "tokens.sqlite")
    try:
        with lock:
            vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
            hypersync = configure_hypersync_from_env(web3)
            vault, detection, row = fetch_frankencoin_shares_metadata(web3, vault_db, token_cache, hypersync.hypersync_client, metadata_block)
            if scan_prices:
                staged_prices = stage_directory / parquet_path.name
                if parquet_path.exists():
                    logger.info("Copying existing prices to %s before the address-scoped scan", staged_prices)
                    shutil.copy2(parquet_path, staged_prices)
                result = fetch_frankencoin_shares_prices(
                    vault,
                    rpc_url=rpc_url,
                    output_path=staged_prices,
                    token_cache=token_cache,
                    hypersync_client=hypersync.hypersync_client,
                    timestamp_cache_path=timestamp_cache_path,
                    start_block=start_block,
                    end_block=end_block,
                    max_workers=max_workers,
                )
                logger.info("FCS price backfill result:\n%s", pformat_scan_result(result))
            apply_frankencoin_shares_metadata(vault_db, detection, row)
            staged_metadata = stage_directory / vault_db_path.name
            vault_db.write(staged_metadata)
            token_cache.commit()
            if not dry_run:
                if vault_db_path.exists():
                    backup_path = Path(f"{vault_db_path}.bak-fcs-{stage_directory.name}")
                    shutil.copy2(vault_db_path, backup_path)
                    logger.info("Metadata backup: %s", backup_path)
                if scan_prices:
                    os.replace(staged_prices, parquet_path)
                # Copy the validated metadata without serialising the full
                # database again; atomic_write supports separate filesystems.
                with staged_metadata.open("rb") as source, atomic_write(vault_db_path, mode="wb", overwrite=True) as destination:
                    shutil.copyfileobj(source, destination)
            print(tabulate([{"vault": FRANKENCOIN_SHARES_ADDRESS, "protocol": row["Protocol"], "curator": row["_manager_name"], "tags": ", ".join(sorted(tag.value for tag in row["_strategy_tags"])), "prices": scan_prices, "dry run": dry_run, "staging": str(stage_directory)}], headers="keys", tablefmt="github"))
    finally:
        token_cache.close()
    logger.info("FCS backfill complete; staged outputs retained at %s", stage_directory)


if __name__ == "__main__":
    main()
