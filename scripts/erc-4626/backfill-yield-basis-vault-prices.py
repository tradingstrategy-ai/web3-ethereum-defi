"""Backfill the four reviewed Ethereum YieldBasis LT price histories.

This is the YieldBasis equivalent of ``backfill-gmx-vault-prices.py``. It
uses one reviewed half-open range, prefills the protocol-owned context table,
and invokes the common address-scoped Parquet writer once. It never passes a
reader-state mapping, so scheduled reader progress is preserved.  ``DRY_RUN``
defaults to true and writes an inspectable copy in a temporary directory.
The dense shared block-timestamp cache is read in both modes; it is
infrastructure required to avoid rebuilding millions of Ethereum timestamps,
not a YieldBasis valuation output.

Only infrastructure paths and worker/logging settings are configurable. The
chain, four LT addresses, hourly frequency and historical start policy are
reviewed constants in this script. ``TIMESTAMP_CACHE`` may point to an
equivalent prepopulated dense cache; otherwise the canonical repository cache
folder is used.
"""

import hashlib
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import EVM_BLOCK_TIMES
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import ParquetScanResult, pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, VaultDatabase, get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.historical_context import YieldBasisContextPrefillResult, fetch_and_store_yield_basis_historical_context, get_yield_basis_historical_context_path
from eth_defi.yield_basis.vault import YieldBasisVault

#: Historical output frequency shared with the common hourly pipeline.
YIELD_BASIS_FREQUENCY: str = "1h"

#: Ethereum mainnet chain ID.
YIELD_BASIS_CHAIN_ID: int = 1

#: Earliest launch block across the reviewed products.
YIELD_BASIS_START_BLOCK: int = min(review.first_seen_at_block for review in YIELD_BASIS_ACTIVE_MARKETS.values())


def _hash_file(path: Path) -> str | None:
    """Return a stable digest for a file or ``None`` when it is absent.

    :param path:
        Reader-state file whose immutability is checked around the backfill.
    :return:
        SHA-256 hexadecimal digest, or ``None`` when the file does not exist.
    """

    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_vaults(vault_database: Path, web3: Web3, token_cache: TokenDiskCache, context_path: Path) -> tuple[list[YieldBasisVault], set[str]]:
    """Load exactly the four reviewed YieldBasis rows and construct adapters.

    :param vault_database:
        Common metadata pickle containing reviewed YieldBasis rows.
    :param web3:
        Ethereum connection assigned to each adapter.
    :param token_cache:
        Shared ERC-20 cache assigned to each adapter.
    :param context_path:
        Protocol context database read by each historical adapter.
    :return:
        Concrete adapters and the lowercase address scope passed to the common
        Parquet writer.
    """

    source = VaultDatabase.read(vault_database)
    expected = {review.lt_address.lower() for review in YIELD_BASIS_ACTIVE_MARKETS.values()}
    selected = {detection.address.lower(): (spec, detection) for spec, row in source.rows.items() if (detection := row["_detection_data"]).chain == YIELD_BASIS_CHAIN_ID and ERC4626Feature.yield_basis_lt in detection.features}
    missing = sorted(expected - set(selected))
    if missing:
        raise RuntimeError(f"Metadata is missing reviewed YieldBasis LTs: {missing}")
    if set(selected) != expected:
        extra = sorted(set(selected) - expected)
        raise RuntimeError(f"Metadata contains unexpected YieldBasis LTs in the fixed backfill scope: {extra}")

    vaults: list[YieldBasisVault] = []
    for address in sorted(expected):
        _spec, detection = selected[address]
        vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
        if not isinstance(vault, YieldBasisVault):
            raise RuntimeError(f"Could not construct YieldBasis adapter for {address}")
        vault.first_seen_at_block = detection.first_seen_at_block
        vault.historical_context_path = context_path
        vaults.append(vault)
    return vaults, expected


def run_backfill(  # noqa: PLR0914
    *,
    vault_database: Path,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
    timestamp_cache_path: Path,
    max_workers: int,
) -> tuple[YieldBasisContextPrefillResult, ParquetScanResult]:
    """Run one complete YieldBasis backfill.

    The selected output paths determine whether this is a retained dry run or
    a persistent operation. Scheduled reader state is hashed before and after
    the common writer and any change aborts the command.

    :param vault_database:
        Common metadata pickle containing all four reviewed rows.
    :param price_database:
        Raw Parquet file updated only for reviewed LT addresses.
    :param context_database:
        YieldBasis contextual-history DuckDB file.
    :param token_cache_path:
        Persistent or dry-run ERC-20 cache path.
    :param timestamp_cache_path:
        Cache-aware Hypersync block-timestamp database directory.
    :param max_workers:
        Maximum threaded archive-state readers.
    :return:
        Context-prefill and common Parquet-writer results.
    """

    rpc_url = read_json_rpc_url(YIELD_BASIS_CHAIN_ID)
    web3 = create_multi_provider_web3(rpc_url)
    safe_head = get_almost_latest_block_number(web3)
    start_block = YIELD_BASIS_START_BLOCK
    end_block = safe_head
    if end_block <= start_block:
        raise RuntimeError(f"Safe head {end_block:,} precedes YieldBasis start block {start_block:,}")
    token_cache = TokenDiskCache(token_cache_path)
    reader_state_path = DEFAULT_READER_STATE_DATABASE
    reader_state_before = _hash_file(reader_state_path)
    try:
        context_path = context_database
        vaults, addresses = _select_vaults(vault_database, web3, token_cache, context_path)
        hypersync = configure_hypersync_from_env(web3)
        block_time = EVM_BLOCK_TIMES[YIELD_BASIS_CHAIN_ID]
        step = int(3600 / block_time)
        context_result = fetch_and_store_yield_basis_historical_context(
            web3=web3,
            vaults=vaults,
            start_block=start_block,
            end_block=end_block,
            step=step,
            max_workers=max_workers,
            context_path=context_path,
            hypersync_client=hypersync.hypersync_client,
            timestamp_cache_path=timestamp_cache_path,
        )
        price_database.parent.mkdir(parents=True, exist_ok=True)
        writer_result = scan_historical_prices_to_parquet(
            output_fname=price_database,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(rpc_url),
            vaults=vaults,
            token_cache=token_cache,
            start_block=start_block,
            end_block=end_block,
            max_workers=max_workers,
            frequency=YIELD_BASIS_FREQUENCY,
            hypersync_client=hypersync.hypersync_client,
            timestamp_cache_file=timestamp_cache_path,
            vault_addresses=addresses,
        )
        token_cache.commit()
        reader_state_after = _hash_file(reader_state_path)
        if reader_state_before != reader_state_after:
            message = "YieldBasis backfill changed the scheduled reader-state pickle"
            raise RuntimeError(message)
        return context_result, writer_result
    finally:
        token_cache.close()


def main() -> None:  # noqa: PLR0914
    """Resolve paths, run the backfill and print a product summary.

    Dry-run mode isolates the mutable Parquet, context and token-cache outputs
    in one retained directory while reusing the dense timestamp cache. Applied
    mode writes the configured pipeline files under the scanner lock.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    production_price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    production_context_database = Path(os.environ.get("CONTEXT_DATABASE", get_yield_basis_historical_context_path())).expanduser()
    production_token_cache = Path(os.environ.get("TOKEN_CACHE", TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)).expanduser()
    # The dense timestamp cache is shared infrastructure, not a YieldBasis
    # valuation output. Reuse it in dry runs as well as persistent runs. An
    # empty temporary cache would attempt thousands of exact Hypersync reads,
    # invite rate limiting and violate the operator rule that historical
    # backfills start from the preserved dense per-chain cache.
    timestamp_cache_path = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))

    retained_directory: Path | None = None
    if dry_run:
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        retained_directory = Path(tempfile.mkdtemp(prefix="yield-basis-backfill-", dir=pipeline_dir))
        price_database = retained_directory / production_price_database.name
        context_database = retained_directory / production_context_database.name
        token_cache_path = retained_directory / "tokens.sqlite"
        if production_price_database.exists():
            shutil.copy2(production_price_database, price_database)
    else:
        price_database = production_price_database
        context_database = production_context_database
        token_cache_path = production_token_cache

    lock = nullcontext() if dry_run else wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60)
    with lock:
        context_result, writer_result = run_backfill(
            vault_database=vault_database,
            price_database=price_database,
            context_database=context_database,
            token_cache_path=token_cache_path,
            timestamp_cache_path=timestamp_cache_path,
            max_workers=max_workers,
        )

    rows = [(review.market_id, review.asset_symbol, review.lt_address) for review in YIELD_BASIS_ACTIVE_MARKETS.values()]
    print(tabulate(rows, headers=("Market", "Underlying token", "LT address"), tablefmt="rounded_outline"))
    print(f"Context observations fetched={context_result.observations_fetched}, inserted={context_result.observations_inserted}")
    print(pformat_scan_result(writer_result))
    if retained_directory is not None:
        print(f"Dry-run files retained at: {retained_directory}")
        print(f"REQUIRE_ALL_PRODUCTS=true VAULT_DATABASE={vault_database} PRICE_DATABASE={price_database} CONTEXT_DATABASE={context_database} poetry run python scripts/erc-4626/examine-yield-basis-vault-backfill.py")
        print(f"VAULT_DATABASE={vault_database} PRICE_DATABASE={price_database} CONTEXT_DATABASE={context_database} poetry run python scripts/erc-4626/examine-yield-basis-performance.py")
    else:
        print(f"Written: {price_database} and {context_database}")


if __name__ == "__main__":
    main()
