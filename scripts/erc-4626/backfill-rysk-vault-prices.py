"""Backfill the fixed Rysk Premium migration scope through the common writer.

This is the historical-data half of the one-off Rysk production migration.
Its eight reviewed targets, deployment blocks, chain order, hourly frequency
and worker count are code constants rather than operator-selectable scope.
Share-price history comes exclusively from onchain ``EpochPriceSet``,
``EpochPriceDisputed`` and ``epochExecuted`` logs streamed through Hypersync.
The script does not read or write vault metadata or reader state.

Usage::

    # Default: real reads, temporary outputs, no persistent changes
    poetry run python scripts/erc-4626/backfill-rysk-vault-prices.py

    # Apply the exact reviewed migration
    DRY_RUN=false poetry run python scripts/erc-4626/backfill-rysk-vault-prices.py

Environment variables:

- ``DRY_RUN``: use temporary context, price, token and timestamp-cache files;
  defaults to ``true``.
- Standard Ethereum and HyperEVM RPC, Hypersync and logging environment
  variables are infrastructure configuration, not migration scope.
"""

import logging
import os
import tempfile
from pathlib import Path

from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import fetch_and_store_rysk_premium_history
from eth_defi.erc_4626.vault_protocol.rysk.migration import RYSK_MIGRATION_CHAIN_IDS, RyskMigrationPool, iter_rysk_migration_pools, parse_rysk_migration_dry_run
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import get_pipeline_data_dir

RYSK_FEATURES = {ERC4626Feature.rysk_premium_like, ERC4626Feature.share_price_equivalence}
RYSK_BACKFILL_FREQUENCY = "1h"
RYSK_BACKFILL_MAX_WORKERS = 4

logger = logging.getLogger(__name__)


def fetch_rysk_full_backfill_range(web3: Web3, pools: tuple[RyskMigrationPool, ...]) -> tuple[int, int]:
    """Resolve the complete safe range for one fixed Rysk chain scope.

    Rysk value events cannot predate their pool deployments. The common safe
    head leaves the provider-specific confirmation margin used by ordinary
    historical scans.

    :param web3:
        Ethereum or HyperEVM connection.
    :param pools:
        Reviewed targets on the connected chain.
    :return:
        Inclusive earliest deployment and exclusive reorg-safe end block.
    """

    if not pools:
        message = "Rysk backfill range needs at least one reviewed pool"
        raise ValueError(message)
    return min(pool.deployment_block for pool in pools), get_almost_latest_block_number(web3)


def _run_backfill(
    *,
    chain_id: int,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
    timestamp_cache_path: Path,
) -> None:
    """Backfill every reviewed Rysk pool on one migration chain.

    :param chain_id:
        Fixed Ethereum or HyperEVM chain identifier.
    :param price_database:
        Common raw historical-price Parquet output.
    :param context_database:
        Shared contextual-history DuckDB output.
    :param token_cache_path:
        Token metadata cache path.
    :param timestamp_cache_path:
        Per-chain execution-block timestamp-cache directory.
    :return:
        None.
    """

    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    if web3.eth.chain_id != chain_id:
        raise RuntimeError(f"Rysk migration RPC returned chain ID {web3.eth.chain_id}, expected {chain_id}")

    pools = tuple(iter_rysk_migration_pools(chain_id))
    start_block, end_block = fetch_rysk_full_backfill_range(web3, pools)
    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        raise RuntimeError(f"Rysk Premium backfill on chain {chain_id} requires Hypersync")

    prefill = fetch_and_store_rysk_premium_history(
        web3=web3,
        hypersync_client=hypersync_client,
        pool_start_blocks={pool.address: pool.deployment_block for pool in pools},
        end_block=end_block,
        context_path=context_database,
        timestamp_cache_path=timestamp_cache_path,
    )

    token_cache = TokenDiskCache(token_cache_path)
    try:
        vaults = []
        for pool in pools:
            vault = RyskVault(web3, VaultSpec(chain_id, pool.address), token_cache=token_cache, features=RYSK_FEATURES)
            vault.first_seen_at_block = pool.deployment_block
            vault.historical_context_path = context_database
            vaults.append(vault)

        price_database.parent.mkdir(parents=True, exist_ok=True)
        result = scan_historical_prices_to_parquet(
            output_fname=price_database,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(rpc_url),
            vaults=vaults,
            token_cache=token_cache,
            start_block=start_block,
            end_block=end_block,
            max_workers=RYSK_BACKFILL_MAX_WORKERS,
            frequency=RYSK_BACKFILL_FREQUENCY,
            hypersync_client=hypersync_client,
            vault_addresses={vault.address.lower() for vault in vaults},
        )
        token_cache.commit()
    finally:
        token_cache.close()

    print(f"Rysk chain {chain_id}: pools={len(pools)}, final epochs fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}")


def main() -> None:
    """Backfill the complete fixed Rysk migration scope.

    The persistent path holds the normal scan-pipeline writer lock for both
    chains. Dry-run mode exercises real RPC and Hypersync reads but writes only
    into one temporary directory.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_rysk_migration_dry_run(os.environ.get("DRY_RUN"))
    pipeline_dir = get_pipeline_data_dir()

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="rysk-backfill-") as directory:
            temporary = Path(directory)
            for chain_id in RYSK_MIGRATION_CHAIN_IDS:
                _run_backfill(
                    chain_id=chain_id,
                    price_database=temporary / "vault-prices-1h.parquet",
                    context_database=temporary / "vault-historical-context.duckdb",
                    token_cache_path=temporary / "tokens.sqlite",
                    timestamp_cache_path=temporary / "block-timestamp",
                )
            print("Dry run complete; production metadata, reader state, prices, context and caches were not changed")
        return

    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60):
        for chain_id in RYSK_MIGRATION_CHAIN_IDS:
            _run_backfill(
                chain_id=chain_id,
                price_database=pipeline_dir / "vault-prices-1h.parquet",
                context_database=pipeline_dir / "vault-historical-context.duckdb",
                token_cache_path=TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH,
                timestamp_cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
            )


if __name__ == "__main__":
    main()
