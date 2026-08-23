"""Run a bounded GMX V2 backfill through the common vault-price writer.

The script is deliberately stateless: it never reads or writes the production
reader-state pickle.  Re-run the same half-open block range to retry it.

Environment variables:

- ``CHAIN``: Required, ``arbitrum`` or ``avalanche``.
- ``START_BLOCK`` / ``END_BLOCK``: Required half-open block range.
- ``FREQUENCY``: ``1h`` (default) or ``1d``.
- ``MAX_WORKERS``: Historical reader worker count. Defaults to 4.
- ``VAULT_ADDRESSES``: Optional comma-separated GM/GLV address subset.
- ``VAULT_DATABASE``, ``UNCLEANED_PRICE_DATABASE``, ``CONTEXT_DATABASE``:
  Optional paths, defaulting under ``PIPELINE_DATA_DIR``.
- ``DRY_RUN``: Set to ``true`` to write only temporary files.
"""

import os
import tempfile
from pathlib import Path

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gmx.historical_context import fetch_and_store_gmx_historical_share_prices
from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

CHAIN_IDS_BY_NAME = {name: chain_id for chain_id, name in GMX_CHAIN_NAMES_BY_ID.items()}
GMX_FEATURES = {ERC4626Feature.gmx_gm, ERC4626Feature.gmx_glv}


def _run_backfill(
    *,
    chain_name: str,
    start_block: int,
    end_block: int,
    frequency: str,
    max_workers: int,
    vault_database: Path,
    price_database: Path,
    context_database: Path,
    selected_addresses: set[str] | None,
    token_cache_path: Path | None = None,
) -> None:
    """Prefill GMX value events and invoke the ordinary Parquet scan once."""

    chain_id = CHAIN_IDS_BY_NAME[chain_name]
    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    hypersync = configure_hypersync_from_env(web3)
    token_cache = TokenDiskCache(token_cache_path) if token_cache_path is not None else TokenDiskCache()
    vault_db = VaultDatabase.read(vault_database)

    detections = [row["_detection_data"] for row in vault_db.rows.values() if row["_detection_data"].chain == chain_id and row["_detection_data"].features & GMX_FEATURES and (selected_addresses is None or row["_detection_data"].address.lower() in selected_addresses)]
    if not detections:
        raise RuntimeError(f"No seeded GMX V2 products found for {chain_name}")
    if selected_addresses is not None:
        found_addresses = {detection.address.lower() for detection in detections}
        missing_addresses = sorted(selected_addresses - found_addresses)
        if missing_addresses:
            raise ValueError(f"Selected addresses are not seeded GMX V2 products on {chain_name}: {missing_addresses}")

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

    if not vaults:
        raise RuntimeError(f"No selected GMX V2 product was deployed during [{start_block}, {end_block})")

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
        frequency=frequency,
        hypersync_client=hypersync.hypersync_client,
        vault_addresses=addresses,
    )
    token_cache.commit()
    print(f"GMX observations fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}")


def main() -> None:
    """Read environment configuration and run one bounded backfill."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    chain_name = os.environ.get("CHAIN", "").strip().lower()
    if chain_name not in CHAIN_IDS_BY_NAME:
        message = "Set CHAIN to arbitrum or avalanche"
        raise ValueError(message)
    start_block = int(os.environ["START_BLOCK"])
    end_block = int(os.environ["END_BLOCK"])
    if not 1 <= start_block < end_block:
        message = "Require 1 <= START_BLOCK < END_BLOCK"
        raise ValueError(message)
    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        message = "FREQUENCY must be 1h or 1d"
        raise ValueError(message)
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    selected = os.environ.get("VAULT_ADDRESSES")
    selected_addresses = {address.strip().lower() for address in selected.split(",") if address.strip()} if selected else None

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="gmx-backfill-") as directory:
            temporary = Path(directory)
            _run_backfill(
                chain_name=chain_name,
                start_block=start_block,
                end_block=end_block,
                frequency=frequency,
                max_workers=max_workers,
                vault_database=vault_database,
                price_database=temporary / price_database.name,
                context_database=temporary / context_database.name,
                selected_addresses=selected_addresses,
                token_cache_path=temporary / "tokens.sqlite",
            )
            print("Dry run complete; production price and context files were not changed")
        return

    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60):
        _run_backfill(
            chain_name=chain_name,
            start_block=start_block,
            end_block=end_block,
            frequency=frequency,
            max_workers=max_workers,
            vault_database=vault_database,
            price_database=price_database,
            context_database=context_database,
            selected_addresses=selected_addresses,
        )


if __name__ == "__main__":
    main()
