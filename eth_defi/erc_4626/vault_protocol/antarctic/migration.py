"""Bounded Antarctic metadata repair and event-history backfill.

Only the two reviewed Arbitrum LP token identities are rewritten. Reader state
is never removed or reset, and the common writer receives an explicit address
scope. See README-Antarctic.md for production volume and locking requirements.
"""

import logging
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_CHAIN_ID, ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore, fetch_and_store_antarctic_history
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir


def parse_antarctic_migration_dry_run(value: str | None) -> bool:
    """Parse the migration mode, defaulting to a non-persistent rehearsal.

    Invalid input fails before a store is opened or a network read begins.

    :param value: Raw DRY_RUN environment value.
    :return: True unless an explicit false literal was supplied.
    """
    if value is None:
        return True
    if value.strip().lower() in {"true", "yes", "1"}:
        return True
    if value.strip().lower() in {"false", "no", "0"}:
        return False
    msg = "DRY_RUN must be true or false"
    raise ValueError(msg)


def fetch_antarctic_metadata_replacements(web3: Web3, database: VaultDatabase, token_cache: TokenDiskCache) -> Mapping[VaultSpec, tuple[VaultRow, PotentialVaultMatch]]:
    """Prepare both target rows before mutating any metadata database.

    Creation dates come from the explorer/Hypersync-verified registry. Existing
    target rows are merge bases so adapter-unowned annotations are preserved.

    :param web3: Configured Arbitrum connection.
    :param database: Existing shared metadata and leads.
    :param token_cache: Token metadata cache.
    :return: Validated target row and lead replacements.
    """
    if web3.eth.chain_id != ANTARCTIC_CHAIN_ID:
        msg = "Antarctic migration requires Arbitrum"
        raise ValueError(msg)
    block = web3.eth.block_number
    replacements = {}
    for deployment in ANTARCTIC_DEPLOYMENTS:
        spec = VaultSpec(ANTARCTIC_CHAIN_ID, deployment.address)
        old = database.rows.get(spec, {})
        previous = old.get("_detection_data")
        features = detect_vault_features(web3, deployment.address, verbose=False)
        detection = ERC4262VaultDetection(chain=ANTARCTIC_CHAIN_ID, address=deployment.address, features=features, first_seen_at_block=deployment.deployment_block, first_seen_at=deployment.deployed_at, updated_at=native_datetime_utc_now(), deposit_count=getattr(previous, "deposit_count", 0), redeem_count=getattr(previous, "redeem_count", 0), configuration_count=getattr(previous, "configuration_count", 0))
        row = create_vault_scan_record(web3, detection, block, token_cache)
        if row["Protocol"] != "Antarctic" or row["Name"].startswith("<broken:"):
            raise RuntimeError(f"Antarctic metadata rebuild failed for {spec}")
        lead = PotentialVaultMatch(chain=ANTARCTIC_CHAIN_ID, address=deployment.address, first_seen_at_block=deployment.deployment_block, first_seen_at=deployment.deployed_at, deposit_count=detection.deposit_count, withdrawal_count=detection.redeem_count, configuration_count=detection.configuration_count)
        replacements[spec] = (old | row, lead)
    return replacements


logger = logging.getLogger(__name__)


def main() -> None:  # noqa: PLR0914
    """Run a locked, copied-state rehearsal or explicit address-scoped apply.

    Network prefill and atomic price writing complete before metadata is
    published. Dry runs copy existing prices/context into the mounted volume
    and use private token and timestamp caches.

    :return: None.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_antarctic_migration_dry_run(os.environ.get("DRY_RUN"))
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers <= 0:
        msg = "MAX_WORKERS must be positive"
        raise ValueError(msg)
    directory = get_pipeline_data_dir()
    metadata_path = directory / "vault-metadata-db.pickle"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)
    with wait_other_writers(directory / "scan-pipeline", timeout=60), tempfile.TemporaryDirectory(prefix="antarctic-migration-", dir=directory) as temporary:
        staging = Path(temporary)
        prices = directory / "vault-prices-1h.parquet"
        context = directory / "vault-historical-context.duckdb"
        if dry_run:
            copy_paths = (prices, context, Path(str(context) + ".wal"))
            required = 2 * sum(path.stat().st_size for path in copy_paths if path.exists())
            if shutil.disk_usage(staging).free < required:
                msg = "Insufficient space for copied-state Antarctic rehearsal"
                raise RuntimeError(msg)
            for path in copy_paths:
                if path.exists():
                    logger.info("Copying %s for dry-run rehearsal", path)
                    shutil.copy2(path, staging / path.name)
            prices, context = staging / prices.name, staging / context.name
        web3 = create_multi_provider_web3(read_json_rpc_url(ANTARCTIC_CHAIN_ID))
        client = configure_hypersync_from_env(web3).hypersync_client
        if client is None:
            msg = "Antarctic migration requires Hypersync"
            raise RuntimeError(msg)
        cache = TokenDiskCache(staging / "tokens.sqlite" if dry_run else TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)
        try:
            database = VaultDatabase.read(metadata_path)
            replacements = fetch_antarctic_metadata_replacements(web3, database, cache)
            end = get_almost_latest_block_number(web3)
            fetch_and_store_antarctic_history(web3=web3, hypersync_client=client, pool_start_blocks={d.address: d.manager_deployment_block for d in ANTARCTIC_DEPLOYMENTS}, end_block=end, context_path=context, timestamp_cache_path=staging / "block-timestamp" if dry_run else DEFAULT_TIMESTAMP_CACHE_FOLDER, force_backfill=True)
            vaults = []
            for deployment in ANTARCTIC_DEPLOYMENTS:
                vault = AntarcticVault(web3, VaultSpec(ANTARCTIC_CHAIN_ID, deployment.address), token_cache=cache)
                vault.first_seen_at_block = deployment.deployment_block
                vault.historical_context_path = context
                vaults.append(vault)
            result = scan_historical_prices_to_parquet(output_fname=prices, web3=web3, web3factory=MultiProviderWeb3Factory(read_json_rpc_url(ANTARCTIC_CHAIN_ID)), vaults=vaults, token_cache=cache, start_block=min(d.deployment_block for d in ANTARCTIC_DEPLOYMENTS), end_block=end, max_workers=max_workers, frequency="1h", hypersync_client=client, timestamp_cache_file=staging / "block-timestamp" if dry_run else DEFAULT_TIMESTAMP_CACHE_FOLDER, vault_addresses={d.address for d in ANTARCTIC_DEPLOYMENTS})
            with AntarcticHistoricalContextStore(context) as store:
                for deployment in ANTARCTIC_DEPLOYMENTS:
                    store.acknowledge_repair(deployment.address, end)
            for spec, (row, lead) in replacements.items():
                database.rows[spec] = row
                database.leads[spec] = lead
            database.write(staging / metadata_path.name if dry_run else metadata_path)
            cache.commit()
            print(tabulate([(d.product, d.address, "dry run" if dry_run else "applied") for d in ANTARCTIC_DEPLOYMENTS], headers=("Product", "Vault", "Mode")))  # noqa: T201
            logger.info("Antarctic historical rows written=%s; unrelated reader state was not changed", result["rows_written"])
        finally:
            cache.close()
    logger.info("Antarctic migration complete; dry_run=%s", dry_run)
