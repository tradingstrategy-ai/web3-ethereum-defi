"""Backfill final Rysk Premium epoch prices through the common writer.

The script uses the Rysk application catalogue only to enumerate current
user-facing products. Share-price history comes exclusively from onchain
``EpochPriceSet``, ``EpochPriceDisputed`` and ``epochExecuted`` logs streamed
through Hypersync. It does not read or write vault reader state or metadata.

Environment variables:

- ``CHAINS``: Comma-separated ``ethereum`` and/or ``hyperliquid``. Defaults to
  both; ``hyperliquid`` denotes HyperEVM chain ID 999.
- ``MAX_WORKERS``: Common historical writer worker count. Defaults to 4.
- ``UNCLEANED_PRICE_DATABASE`` and ``CONTEXT_DATABASE``: Optional storage
  paths under ``PIPELINE_DATA_DIR``.
- ``TOKEN_CACHE``: Optional token metadata cache path.
- ``TIMESTAMP_CACHE``: Optional shared per-chain timestamp-cache directory.
- ``DRY_RUN``: Use temporary context, price, token and timestamp-cache files.
"""

import logging
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.rysk.api import fetch_rysk_premium_pools, is_rysk_premium_test_pool
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import fetch_and_store_rysk_premium_history
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

RYSK_CHAIN_IDS_BY_NAME = {"ethereum": 1, "hyperliquid": 999}
RYSK_FEATURES = {ERC4626Feature.rysk_premium_like, ERC4626Feature.share_price_equivalence}
RYSK_FULL_HISTORY_START_BLOCK = 1
RYSK_BACKFILL_FREQUENCY = "1h"

logger = logging.getLogger(__name__)


def fetch_rysk_full_backfill_range(web3: Web3) -> tuple[int, int]:
    """Resolve the complete safe range for one Rysk chain.

    The reader emits only final source events, so a block-one lower bound does
    not manufacture observations or request historical contract state.

    :param web3:
        Ethereum or HyperEVM connection.
    :return:
        Inclusive start and exclusive reorg-safe end block.
    """

    return RYSK_FULL_HISTORY_START_BLOCK, get_almost_latest_block_number(web3)


def _run_backfill(
    *,
    chain_name: str,
    max_workers: int,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
    timestamp_cache_path: Path,
) -> None:
    """Backfill current public Rysk pools on one configured chain.

    :param chain_name:
        ``ethereum`` or ``hyperliquid``.
    :param max_workers:
        Common historical writer worker count.
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

    chain_id = RYSK_CHAIN_IDS_BY_NAME[chain_name]
    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    if web3.eth.chain_id != chain_id:
        raise RuntimeError(f"RPC for {chain_name} returned chain ID {web3.eth.chain_id}, expected {chain_id}")

    pools = tuple(pool for pool in fetch_rysk_premium_pools() if pool.chain_id == chain_id and not is_rysk_premium_test_pool(pool))
    if not pools:
        raise RuntimeError(f"Rysk Premium catalogue has no public pools for {chain_name}")

    start_block, end_block = fetch_rysk_full_backfill_range(web3)
    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        raise RuntimeError(f"Rysk Premium backfill on {chain_name} requires Hypersync")

    prefill = fetch_and_store_rysk_premium_history(
        web3=web3,
        hypersync_client=hypersync_client,
        pool_start_blocks={pool.address: start_block for pool in pools},
        end_block=end_block,
        context_path=context_database,
        timestamp_cache_path=timestamp_cache_path,
    )

    token_cache = TokenDiskCache(token_cache_path)
    try:
        vaults = []
        for pool in pools:
            vault = RyskVault(web3, VaultSpec(chain_id, pool.address), token_cache=token_cache, features=RYSK_FEATURES)
            vault.first_seen_at_block = start_block
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
            max_workers=max_workers,
            frequency=RYSK_BACKFILL_FREQUENCY,
            hypersync_client=hypersync_client,
            vault_addresses={vault.address.lower() for vault in vaults},
        )
        token_cache.commit()
    finally:
        token_cache.close()

    print(f"Rysk {chain_name}: pools={len(pools)}, final epochs fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}")


def main() -> None:
    """Backfill complete onchain Rysk history on selected chains.

    The production path holds the normal scan-pipeline lock. ``DRY_RUN`` uses
    temporary files and leaves configured pipeline data unchanged.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    chain_names = tuple(name.strip().lower() for name in os.environ.get("CHAINS", "ethereum,hyperliquid").split(",") if name.strip())
    unknown = set(chain_names) - set(RYSK_CHAIN_IDS_BY_NAME)
    if unknown:
        raise ValueError(f"Unsupported Rysk chains: {sorted(unknown)}")
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers <= 0:
        raise ValueError(f"MAX_WORKERS must be positive, got {max_workers}")

    pipeline_dir = get_pipeline_data_dir()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    token_cache_path = Path(os.environ.get("TOKEN_CACHE", TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)).expanduser()
    timestamp_cache_path = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="rysk-backfill-") as directory:
            temporary = Path(directory)
            for chain_name in chain_names:
                _run_backfill(
                    chain_name=chain_name,
                    max_workers=max_workers,
                    price_database=temporary / price_database.name,
                    context_database=temporary / context_database.name,
                    token_cache_path=temporary / token_cache_path.name,
                    timestamp_cache_path=temporary / "block-timestamp",
                )
            print("Dry run complete; configured pipeline files were not changed")
        return

    lock = wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60) if price_database.parent == pipeline_dir else nullcontext()
    with lock:
        for chain_name in chain_names:
            _run_backfill(
                chain_name=chain_name,
                max_workers=max_workers,
                price_database=price_database,
                context_database=context_database,
                token_cache_path=token_cache_path,
                timestamp_cache_path=timestamp_cache_path,
            )


if __name__ == "__main__":
    main()
