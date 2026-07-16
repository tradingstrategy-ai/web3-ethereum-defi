#!/usr/bin/env python3
"""Backfill Securitize DSToken leads and supported price history.

This migration preserves unrelated vault database rows, reader states and
Parquet histories. It upserts the reviewed products in
``SECURITIZE_PRODUCTS`` and rewrites price history only for products with an
explicit adapter NAV estimate. DSToken contracts do not expose a universal
fund-NAV method, so unpriced products are registered as leads but excluded from
the price scan.

Run with::

    source .local-test.env && poetry run python scripts/securitize/backfill-history.py

Environment variables:

``DRY_RUN``
    Show the plan without writing data. Default: ``false``.
``SECURITIZE_SCAN_PRICES``
    Scan DSTokens with a reviewed estimated or external NAV source. Default:
    ``true``.
``SECURITIZE_PRODUCTS``
    Optional comma-separated DSToken addresses to backfill.
``SECURITIZE_CLEAN_PRICES``
    Rebuild selected cleaned histories after the raw scan. Default: ``true``.
``FREQUENCY``
    Historical sample interval, ``1h`` or ``1d``. Default: ``1d``.
``START_BLOCK`` / ``END_BLOCK``
    Optional inclusive block boundaries for a carefully scoped run.
``MAX_WORKERS``
    Historical multicall worker count. Default: ``8``.
``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE``
and ``READER_STATE_DATABASE``
    Optional paths for isolated tests or production-data overrides.

The script reads archive-capable RPC URLs from ``JSON_RPC_<CHAIN_NAME>``.
"""

import datetime
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampDatabase
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.securitize.description import SECURITIZE_PRODUCTS, SecuritizeProduct
from eth_defi.securitize.share_price import create_securitize_share_price_transformer_factory
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment setting.

    The migration is operated through environment variables so it can run from
    the same production wrapper as other vault scripts without another command
    line parser.

    :param name:
        Environment variable to parse.
    :param default:
        Value used when the variable is absent.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_optional_int_env(name: str) -> int | None:
    """Read an optional integer block boundary from the environment.

    Operators can use this only to narrow a controlled rerun. The normal path
    discovers the first contract-code block automatically.

    :param name:
        Environment variable to parse.
    :return:
        Parsed integer, or ``None`` when it is unset.
    """

    value = os.environ.get(name)
    return int(value) if value else None


def parse_path_env(name: str, default: Path) -> Path:
    """Read a filesystem override while retaining production defaults.

    Isolated test runs use this to keep state files out of the normal vault
    pipeline directory.

    :param name:
        Environment variable holding a path.
    :param default:
        Default production path.
    :return:
        Expanded configured path.
    """

    value = os.environ.get(name)
    return Path(value).expanduser() if value else default.expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Resolve the requested historical sampling frequency.

    The execution plan and the price scanner use the same validated value so a
    displayed daily run cannot silently perform hourly work.

    :return:
        One-hour or one-day scan interval.
    :raises ValueError:
        If the requested interval is unsupported.
    """

    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")
    return cast(Literal["1h", "1d"], frequency)


def iter_products() -> Iterable[SecuritizeProduct]:
    """Iterate every reviewed Securitize product exactly once.

    The registry is keyed for direct chain-and-address lookup, so guard against
    an accidental duplicate product when future aliases are added.

    :return:
        Unique registry products.
    """

    selected_addresses = {address.strip().lower() for address in os.environ.get("SECURITIZE_PRODUCTS", "").split(",") if address.strip()}
    seen: set[tuple[int, HexAddress]] = set()
    for product in SECURITIZE_PRODUCTS.values():
        if selected_addresses and product.token.lower() not in selected_addresses:
            continue
        key = product.chain_id, product.token
        if key not in seen:
            seen.add(key)
            yield product


def fetch_contract_deployment_block(web3: Web3, address: HexAddress, end_block: int) -> int:
    """Find the first block where an address has deployed contract code.

    The reviewed DSToken registry does not duplicate a manually maintained
    creation-block list. An archive-node binary search yields the first code
    block in logarithmic RPC calls and remains correct when a new product is
    added to the registry.

    :param web3:
        Archive-capable connection for the product chain.
    :param address:
        DSToken proxy address.
    :param end_block:
        Highest block that may be inspected.
    :return:
        First block with non-empty runtime code.
    :raises ValueError:
        If the address has no contract code at ``end_block``.
    """

    address = Web3.to_checksum_address(address)
    if not web3.eth.get_code(address, block_identifier=end_block):
        raise ValueError(f"No contract code for Securitize product {address} at block {end_block}")

    low = 0
    high = end_block
    while low < high:
        middle = (low + high) // 2
        if web3.eth.get_code(address, block_identifier=middle):
            high = middle
        else:
            low = middle + 1
    return low


def fetch_product_first_seen_at(web3: Web3, deployment_block: int) -> datetime.datetime:
    """Read the naive UTC timestamp for a product deployment block.

    Shared lead records carry both the discovery block and its timestamp. The
    timestamp comes from the same archive endpoint used to locate deployment.

    :param web3:
        Archive-capable connection for the product chain.
    :param deployment_block:
        First block with the product's runtime code.
    :return:
        Naive UTC deployment timestamp.
    """

    timestamp = web3.eth.get_block(deployment_block)["timestamp"]
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)


def create_detection(product: SecuritizeProduct, deployment_block: int, first_seen_at: datetime.datetime) -> ERC4262VaultDetection:
    """Create a scanner-compatible detection for a reviewed DSToken.

    A migration uses the product registry as its authoritative lead source,
    rather than reprocessing unrelated discovery events from the chain.

    :param product:
        Reviewed Securitize product.
    :param deployment_block:
        First block containing the DSToken proxy code.
    :param first_seen_at:
        Deployment timestamp in naive UTC.
    :return:
        Detection record accepted by metadata extraction.
    """

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=deployment_block,
        first_seen_at=first_seen_at,
        features={ERC4626Feature.securitize_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_lead(product: SecuritizeProduct, deployment_block: int, first_seen_at: datetime.datetime) -> PotentialVaultMatch:
    """Create a lead row without discovering the whole chain again.

    The lead carries the same initial position as its synthetic detection so
    downstream metadata and history readers retain the expected ordering.

    :param product:
        Reviewed Securitize product.
    :param deployment_block:
        First block containing the DSToken proxy code.
    :param first_seen_at:
        Deployment timestamp in naive UTC.
    :return:
        Lead record for the shared vault database.
    """

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=deployment_block,
        first_seen_at=first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def read_vault_database(path: Path) -> VaultDatabase:
    """Load an existing metadata database without replacing its entries.

    A missing path starts an empty database for isolated use. Existing files
    are read intact and later updated only for selected Securitize rows.

    :param path:
        Metadata database pickle path.
    :return:
        Existing or empty vault database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load saved price-reader states from a trusted local pickle.

    Only selected Securitize states are removed before their histories are
    rewritten; all unrelated states are retained.

    :param path:
        Reader-state pickle path.
    :return:
        Existing states, or an empty mapping when the file is absent.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Persist reader state atomically after the scoped history scan.

    Atomic replacement ensures that an interrupted process cannot leave a
    partially serialised reader-state file.

    :param path:
        Reader-state pickle path.
    :param states:
        Complete state mapping returned by the historical scanner.
    :return:
        None.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def resolve_price_scan_start_block(
    chain_id: int,
    deployment_blocks: Iterable[int],
    timestamp_cache_folder: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
) -> int:
    """Choose a timestamp-cache-compatible start block for selected products.

    A targeted rewrite must begin at deployment, not at the ordinary scanner's
    latest cursor. If the local timestamp cache begins later, clip to its first
    supported block instead of issuing an impossible timestamp lookup.

    :param chain_id:
        EVM chain containing the selected products.
    :param deployment_blocks:
        Deployment blocks for products with a configured price source.
    :param timestamp_cache_folder:
        Directory containing the per-chain timestamp cache.
    :return:
        Explicit override, or the earliest timestamp-cache-compatible block.
    """

    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block

    deployment_start_block = min(deployment_blocks)
    cache_file = BlockTimestampDatabase.get_database_file_chain(chain_id, timestamp_cache_folder)
    if not cache_file.exists():
        return deployment_start_block

    timestamp_cache = BlockTimestampDatabase.load(chain_id, cache_file)
    try:
        first_cached_block = timestamp_cache.get_first_block()
    finally:
        timestamp_cache.close()
    return max(deployment_start_block, first_cached_block)


def build_priced_vaults(web3: Web3, products: Iterable[tuple[SecuritizeProduct, int]], token_cache: TokenDiskCache) -> list[VaultBase]:
    """Build adapters only for DSTokens with a configured historical NAV.

    Unpriced DSTokens must remain leads and metadata rows, but their history
    cannot be inferred from supply alone and therefore must not rewrite price
    parquet rows.

    :param web3:
        Connection for one product chain.
    :param products:
        Product and deployment-block pairs with a price estimate.
    :param token_cache:
        Shared ERC-20 metadata cache.
    :return:
        Vault adapters with their individual history start blocks.
    """

    vaults: list[VaultBase] = []
    for product, deployment_block in products:
        vault = create_vault_instance(
            web3,
            product.token,
            features={ERC4626Feature.securitize_like},
            token_cache=token_cache,
        )
        if vault is None:
            raise RuntimeError(f"Could not create Securitize vault adapter for {product.token}")
        vault.first_seen_at_block = deployment_block
        vaults.append(vault)
    return vaults


def backfill_chain(  # noqa: PLR0914
    chain_id: int,
    products: list[SecuritizeProduct],
    *,
    dry_run: bool,
    scan_prices: bool,
    clean_prices: bool,
    frequency: Literal["1h", "1d"],
    vault_db: VaultDatabase,
    vault_db_path: Path,
    price_database_path: Path,
    cleaned_price_database_path: Path,
    reader_state_database_path: Path,
    token_cache: TokenDiskCache,
) -> dict[str, object]:
    """Upsert one chain's DSToken leads and rewrite only priced histories.

    Product deployments are derived independently for the selected addresses.
    The shared metadata database receives only their rows, and the historical
    scanner receives only priced address filters.

    :param chain_id:
        EVM chain being migrated.
    :param products:
        Reviewed Securitize products on that chain.
    :param dry_run:
        Whether writes to state and Parquet files are disabled.
    :param scan_prices:
        Whether to rebuild supported price histories.
    :param clean_prices:
        Whether to rebuild corresponding cleaned histories.
    :param frequency:
        Historical sampling interval.
    :param vault_db:
        Existing shared metadata database.
    :param vault_db_path:
        Metadata database destination.
    :param price_database_path:
        Uncleaned price Parquet destination.
    :param cleaned_price_database_path:
        Cleaned price Parquet destination.
    :param reader_state_database_path:
        Shared reader-state pickle destination.
    :param token_cache:
        Shared ERC-20 metadata cache.
    :return:
        Tabular migration summary for the chain.
    """

    json_rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_name = get_chain_name(chain_id)
    end_block = parse_optional_int_env("END_BLOCK") or web3.eth.block_number
    logger.info("Backfilling %d Securitize products on %s using %s", len(products), chain_name, get_provider_name(web3.provider))

    product_state = {
        product: (
            fetch_contract_deployment_block(web3, product.token, end_block),
            None,
        )
        for product in products
    }
    product_state = {product: (deployment_block, fetch_product_first_seen_at(web3, deployment_block)) for product, (deployment_block, _) in product_state.items()}
    leads = {product.token: create_lead(product, deployment_block, first_seen_at) for product, (deployment_block, first_seen_at) in product_state.items()}
    rows = {
        VaultSpec(product.chain_id, product.token): create_vault_scan_record(
            web3,
            detection=create_detection(product, deployment_block, first_seen_at),
            block_identifier=end_block,
            token_cache=token_cache,
        )
        for product, (deployment_block, first_seen_at) in product_state.items()
    }

    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.update_leads_and_rows(
            chain_id=chain_id,
            last_scanned_block=end_block,
            leads=leads,
            rows=rows,
        )
        vault_db.write(vault_db_path)

    priced_products = [(product, deployment_block) for product, (deployment_block, _) in product_state.items() if product.estimated_nav_per_share is not None or product.nav_source != "unconfigured"]
    scan_summary = "-"
    if scan_prices and priced_products:
        vault_ids = {product.token.lower() for product, _ in priced_products}
        if dry_run:
            scan_summary = "dry-run"
        else:
            reader_states = read_reader_states(reader_state_database_path)
            reader_states = {spec: state for spec, state in reader_states.items() if spec.vault_address.lower() not in vault_ids}
            start_block = resolve_price_scan_start_block(chain_id, (deployment_block for _, deployment_block in priced_products))
            logger.info("Backfilling %d Securitize products with a NAV source on %s from block %d", len(priced_products), chain_name, start_block)
            hypersync_config = configure_hypersync_from_env(web3)
            priced_vaults = build_priced_vaults(web3, priced_products, token_cache)
            scan_result = scan_historical_prices_to_parquet(
                output_fname=price_database_path,
                web3=web3,
                web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
                vaults=priced_vaults,
                start_block=start_block,
                end_block=end_block,
                max_workers=int(os.environ.get("MAX_WORKERS", "8")),
                chunk_size=32,
                token_cache=token_cache,
                frequency=frequency,
                reader_states=reader_states,
                hypersync_client=hypersync_config.hypersync_client,
                vault_addresses=vault_ids,
                historical_read_transformer_factory=create_securitize_share_price_transformer_factory(priced_vaults, web3),
            )
            write_reader_states(reader_state_database_path, scan_result["reader_states"])
            scan_summary = pformat_scan_result(scan_result)
            if clean_prices:
                cleaned_rows = replace_cleaned_vault_histories(
                    {VaultSpec(product.chain_id, product.token).as_string_id() for product, _ in priced_products},
                    vault_db_path=vault_db_path,
                    raw_price_df_path=price_database_path,
                    cleaned_price_df_path=cleaned_price_database_path,
                    logger=logger.info,
                )
                scan_summary = f"{scan_summary}; cleaned_rows={cleaned_rows:,}"

    return {
        "chain": chain_name,
        "chain_id": chain_id,
        "rpc": get_json_rpc_env(chain_id),
        "products": len(products),
        "priced_products": len(priced_products),
        "metadata_rows": len(rows),
        "scan": scan_summary,
    }


def main() -> None:
    """Run the targeted Securitize lead and price-history migration.

    The full registry is grouped by chain so future Securitize deployments can
    be added without expanding this operational entry point.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/securitize-backfill-history.log"))
    dry_run = parse_bool_env("DRY_RUN")
    scan_prices = parse_bool_env("SECURITIZE_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("SECURITIZE_CLEAN_PRICES", default=True)
    frequency = resolve_frequency()
    products_by_chain: dict[int, list[SecuritizeProduct]] = {}
    for product in iter_products():
        products_by_chain.setdefault(product.chain_id, []).append(product)
    if not products_by_chain:
        message = "No Securitize products are registered"
        raise RuntimeError(message)

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_database_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    plan = [
        {
            "chain": get_chain_name(chain_id),
            "chain_id": chain_id,
            "rpc": get_json_rpc_env(chain_id),
            "products": len(chain_products),
            "priced_products": sum(product.estimated_nav_per_share is not None or product.nav_source != "unconfigured" for product in chain_products),
        }
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]
    print("Securitize backfill plan")
    print(tabulate(plan, headers="keys", tablefmt="github"))

    vault_db = read_vault_database(vault_db_path)
    token_cache = TokenDiskCache()
    summaries = [
        backfill_chain(
            chain_id,
            chain_products,
            dry_run=dry_run,
            scan_prices=scan_prices,
            clean_prices=clean_prices,
            frequency=frequency,
            vault_db=vault_db,
            vault_db_path=vault_db_path,
            price_database_path=price_database_path,
            cleaned_price_database_path=cleaned_price_database_path,
            reader_state_database_path=reader_state_database_path,
            token_cache=token_cache,
        )
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]
    if not dry_run:
        token_cache.commit()
    print("Securitize backfill summary")
    print(tabulate(summaries, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()
