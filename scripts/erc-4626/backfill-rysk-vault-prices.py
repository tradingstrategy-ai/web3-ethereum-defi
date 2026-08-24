"""Backfill Rysk Premium epoch-price history through the common writer.

Rysk Premium pools are DeFi option vaults, not ERC-4626 vaults or legally
structured funds. Their public application API publishes the complete pool
catalogue and epoch snapshots. This script synchronises that catalogue, stores
all snapshots in the shared contextual-history DuckDB and writes sparse,
final-epoch withdrawal-PPS observations to the common raw price Parquet.

The script is stateless: it never reads or writes the production reader-state
pickle. It processes Ethereum and HyperEVM sequentially, using a block-one
lower bound because the contextual reader selects only source-backed epoch
observations. Existing rows are replaced only for the selected Rysk addresses
and requested ranges.

Environment variables:

- ``CHAINS``: Comma-separated ``ethereum`` and/or ``hyperliquid``. Defaults to
  both; ``hyperliquid`` is the repository name for HyperEVM chain ID 999.
- ``MAX_WORKERS``: Common historical reader worker count. Defaults to 4.
- ``VAULT_DATABASE``, ``UNCLEANED_PRICE_DATABASE``, ``CONTEXT_DATABASE``:
  Optional storage paths, defaulting under ``PIPELINE_DATA_DIR``.
- ``TOKEN_CACHE``: Optional token metadata SQLite cache path.
- ``DRY_RUN``: Set to ``true`` to use temporary metadata, context, price and
  token-cache files without changing the configured paths.

See also :doc:`eth_defi.erc_4626.vault_protocol.rysk.README-Rysk-vaults`.
"""

import logging
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.api import fetch_rysk_premium_pools
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import fetch_and_store_rysk_premium_history
from eth_defi.erc_4626.vault_protocol.rysk.vault_sync import fetch_and_sync_rysk_premium_catalogue
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

RYSK_CHAIN_IDS_BY_NAME = {
    "ethereum": 1,
    "hyperliquid": 999,
}
RYSK_FEATURE = ERC4626Feature.rysk_premium_like
RYSK_FULL_HISTORY_START_BLOCK = 1
RYSK_BACKFILL_FREQUENCY = "1h"

logger = logging.getLogger(__name__)


def fetch_rysk_full_backfill_range(web3: Web3) -> tuple[int, int]:
    """Resolve the complete safe range for one Rysk Premium chain.

    The contextual reader returns only source-backed final epoch observations,
    so a block-one lower bound cannot create synthetic observations. The end
    block is resolved once and shared by the snapshot prefill and Parquet
    replacement.

    :param web3:
        Web3 connection for Ethereum or HyperEVM.
    :return:
        Inclusive start and exclusive safe-head end block.
    """

    return RYSK_FULL_HISTORY_START_BLOCK, get_almost_latest_block_number(web3)


def _run_backfill(  # noqa: PLR0914
    *,
    chain_name: str,
    max_workers: int,
    vault_database: Path,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
) -> None:
    """Backfill every current Rysk Premium pool on one configured chain.

    The metadata database is synchronised before the context prefill, so a
    new Rysk pool receives the same classification and scan record as a normal
    scheduled scan. Pool snapshots are then fetched exhaustively, while the
    common writer emits only final epoch exit prices.

    :param chain_name:
        ``ethereum`` or ``hyperliquid``.
    :param max_workers:
        Common historical reader worker count.
    :param vault_database:
        Common vault metadata pickle to create or update.
    :param price_database:
        Common raw historical-price Parquet output.
    :param context_database:
        Shared contextual-history DuckDB output.
    :param token_cache_path:
        Token metadata SQLite cache used for scanner rows.
    :return:
        None.
    """

    chain_id = RYSK_CHAIN_IDS_BY_NAME[chain_name]
    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    detected_chain_id = web3.eth.chain_id
    if detected_chain_id != chain_id:
        message = f"RPC for {chain_name} returned chain ID {detected_chain_id}, expected {chain_id}"
        raise RuntimeError(message)

    start_block, end_block = fetch_rysk_full_backfill_range(web3)
    token_cache = TokenDiskCache(token_cache_path)
    try:
        vault_db = VaultDatabase.read(vault_database) if vault_database.exists() else VaultDatabase()
        catalogue = fetch_and_sync_rysk_premium_catalogue(
            web3=web3,
            vault_db=vault_db,
            token_cache=token_cache,
            block_number=web3.eth.block_number,
        )
        pools = tuple(pool for pool in fetch_rysk_premium_pools() if pool.chain_id == chain_id)
        if not pools:
            message = f"Rysk Premium catalogue has no pools for {chain_name}"
            raise RuntimeError(message)

        logger.info("Prefilling full Rysk Premium snapshot history for %d pools on %s", len(pools), chain_name)
        prefill = fetch_and_store_rysk_premium_history(
            pools=pools,
            context_path=context_database,
        )

        detections = [row["_detection_data"] for row in vault_db.rows.values() if row["_detection_data"].chain == chain_id and RYSK_FEATURE in row["_detection_data"].features]
        if not detections:
            message = f"Metadata synchronisation created no Rysk Premium pools for {chain_name}"
            raise RuntimeError(message)

        vaults = []
        for detection in detections:
            vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
            if vault is None:
                message = f"Could not construct Rysk reader for {detection.address}"
                raise RuntimeError(message)
            # The contextual reader queries only final source observations, so
            # using block one does not request unavailable historic contract
            # state and includes every published epoch for this pool.
            vault.first_seen_at_block = start_block
            vault.historical_context_path = context_database
            vaults.append(vault)

        vault_database.parent.mkdir(parents=True, exist_ok=True)
        vault_db.write(vault_database)

        price_database.parent.mkdir(parents=True, exist_ok=True)
        hypersync = configure_hypersync_from_env(web3)
        result = scan_historical_prices_to_parquet(
            output_fname=price_database,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(rpc_url),
            vaults=vaults,
            token_cache=token_cache,
            start_block=start_block,
            end_block=end_block,
            max_workers=max_workers,
            frequency=RYSK_BACKFILL_FREQUENCY,
            hypersync_client=hypersync.hypersync_client,
            vault_addresses={vault.address.lower() for vault in vaults},
        )
        token_cache.commit()
    finally:
        token_cache.close()

    print(
        f"Rysk {chain_name}: pools={catalogue.pools}, snapshots fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}",
    )


def main() -> None:
    """Backfill complete published Rysk Premium history on selected chains.

    The production path holds the normal scan-pipeline lock. ``DRY_RUN`` uses
    temporary files and does not read or change the configured pipeline data.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    chain_names = tuple(name.strip().lower() for name in os.environ.get("CHAINS", "ethereum,hyperliquid").split(",") if name.strip())
    unknown = set(chain_names) - set(RYSK_CHAIN_IDS_BY_NAME)
    if unknown:
        message = f"Unsupported Rysk chains: {sorted(unknown)}"
        raise ValueError(message)
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers <= 0:
        message = f"MAX_WORKERS must be positive, got {max_workers}"
        raise ValueError(message)

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    token_cache_path = Path(os.environ.get("TOKEN_CACHE", TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="rysk-backfill-") as directory:
            temporary = Path(directory)
            for chain_name in chain_names:
                _run_backfill(
                    chain_name=chain_name,
                    max_workers=max_workers,
                    vault_database=temporary / vault_database.name,
                    price_database=temporary / price_database.name,
                    context_database=temporary / context_database.name,
                    token_cache_path=temporary / token_cache_path.name,
                )
            print("Dry run complete; configured pipeline files were not changed")
        return

    lock = wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60) if vault_database.parent == pipeline_dir else nullcontext()
    with lock:
        for chain_name in chain_names:
            _run_backfill(
                chain_name=chain_name,
                max_workers=max_workers,
                vault_database=vault_database,
                price_database=price_database,
                context_database=context_database,
                token_cache_path=token_cache_path,
            )


if __name__ == "__main__":
    main()
