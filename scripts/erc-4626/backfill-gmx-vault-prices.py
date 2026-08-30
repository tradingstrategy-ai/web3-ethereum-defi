"""Run a full GMX V2 backfill through the common vault-price writer.

The script is deliberately stateless: it never reads or writes the production
reader-state pickle. It processes Arbitrum and Avalanche sequentially from
block 1 to each chain's snapshotted safe head, using hourly price buckets.
Running the script needs no backfill-specific configuration.

Optional environment variables:

- ``MAX_WORKERS``: Historical reader worker count. Defaults to 4.
- ``VAULT_DATABASE``, ``UNCLEANED_PRICE_DATABASE``, ``CONTEXT_DATABASE``:
  Optional paths, defaulting under ``PIPELINE_DATA_DIR``.
- ``DRY_RUN``: Set to ``true`` to write only temporary files.
"""

import os
import tempfile
from pathlib import Path

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gmx.historical_context import fetch_and_store_gmx_historical_share_prices
from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

CHAIN_IDS_BY_NAME = {name: chain_id for chain_id, name in GMX_CHAIN_NAMES_BY_ID.items()}
GMX_BACKFILL_CHAIN_NAMES: tuple[str, ...] = tuple(CHAIN_IDS_BY_NAME)
GMX_FEATURES = {ERC4626Feature.gmx_gm, ERC4626Feature.gmx_glv}
GMX_FULL_HISTORY_START_BLOCK = 1
GMX_BACKFILL_FREQUENCY = "1h"


def fetch_gmx_full_backfill_range(web3: Web3) -> tuple[int, int]:
    """Resolve the complete safe historical range for one GMX chain.

    GMX value events cannot predate the chain, so block 1 is a simple stable
    lower boundary. The common safe-head helper leaves the provider-specific
    confirmation margin used by other historical readers. The returned range
    is half-open and is resolved once for both context collection and Parquet
    replacement.

    :param web3:
        Web3 connection for Arbitrum One or Avalanche.
    :return:
        Inclusive start and exclusive safe-head end block.
    """

    return GMX_FULL_HISTORY_START_BLOCK, get_almost_latest_block_number(web3)


def _run_backfill(
    *,
    chain_name: str,
    max_workers: int,
    vault_database: Path,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path | None = None,
) -> None:
    """Backfill one GMX chain from block 1 through its safe head.

    The source context and common Parquet replacement share one resolved
    half-open range. Every seeded GM and GLV product on the chain is included,
    and hourly buckets are always used.

    :param chain_name:
        ``arbitrum`` or ``avalanche``.
    :param max_workers:
        Historical reader worker count.
    :param vault_database:
        Common vault metadata pickle containing seeded GMX products.
    :param price_database:
        Common raw historical-price Parquet file.
    :param context_database:
        Shared contextual-reader DuckDB containing the GMX-owned table.
    :param token_cache_path:
        Optional isolated token-cache path used by dry runs.
    :return:
        None.
    """

    chain_id = CHAIN_IDS_BY_NAME[chain_name]
    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    start_block, end_block = fetch_gmx_full_backfill_range(web3)
    hypersync = configure_hypersync_from_env(web3)
    token_cache = TokenDiskCache(token_cache_path) if token_cache_path is not None else TokenDiskCache()
    vault_db = VaultDatabase.read(vault_database)

    detections = [row["_detection_data"] for row in vault_db.rows.values() if row["_detection_data"].chain == chain_id and row["_detection_data"].features & GMX_FEATURES]
    if not detections:
        raise RuntimeError(f"No seeded GMX V2 products found for {chain_name}")
    # The full metadata database is large and is not needed during context
    # collection; retain only the selected GMX detection records.
    del vault_db

    prefill = fetch_and_store_gmx_historical_share_prices(
        web3=web3,
        hypersync_client=hypersync.hypersync_client,
        start_block=start_block,
        end_block=end_block,
        context_path=context_database,
        product_addresses=(detection.address for detection in detections),
    )
    vaults = []
    for detection in detections:
        vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
        if vault is None:
            raise RuntimeError(f"Could not construct GMX reader for {detection.address}")
        # Value events cannot exist before deployment, so the requested lower
        # bound is sufficient and avoids archive-state deployment probing.
        vault.first_seen_at_block = start_block
        vault.historical_context_path = context_database
        vaults.append(vault)

    addresses = {vault.address.lower() for vault in vaults}
    price_database.parent.mkdir(parents=True, exist_ok=True)
    result = scan_historical_prices_to_parquet(
        output_fname=price_database,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(rpc_url),
        vaults=vaults,
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
        frequency=GMX_BACKFILL_FREQUENCY,
        hypersync_client=hypersync.hypersync_client,
        vault_addresses=addresses,
    )
    token_cache.commit()
    print(f"GMX observations fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}")


def main() -> None:
    """Backfill complete hourly GMX history on both supported chains.

    Arbitrum runs first and Avalanche second while holding the common pipeline
    writer lock. Optional environment variables only override storage paths,
    worker count, logging and dry-run behaviour; no chain, range or frequency
    selection is needed.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="gmx-backfill-") as directory:
            temporary = Path(directory)
            for chain_name in GMX_BACKFILL_CHAIN_NAMES:
                _run_backfill(
                    chain_name=chain_name,
                    max_workers=max_workers,
                    vault_database=vault_database,
                    price_database=temporary / price_database.name,
                    context_database=temporary / context_database.name,
                    token_cache_path=temporary / "tokens.sqlite",
                )
            print("Dry run complete; production price and context files were not changed")
        return

    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60):
        for chain_name in GMX_BACKFILL_CHAIN_NAMES:
            _run_backfill(
                chain_name=chain_name,
                max_workers=max_workers,
                vault_database=vault_database,
                price_database=price_database,
                context_database=context_database,
            )


if __name__ == "__main__":
    main()
