#!/usr/bin/env python3
"""Backfill historical Asseto vault data into the shared vault pipeline.

This is a targeted production tool for Asseto products registered in
:data:`eth_defi.asseto.constants.ASSETO_PRODUCTS`. It does not rediscover the
whole HashKey Chain and updates only the selected Asseto vault identifiers.
The historical scan reads ERC-20 supply and Asseto's ``Pricer`` NAV/share,
then calculates TVL as ``totalSupply * getLatestPrice``.

Usage:

.. code-block:: shell

    source .local-test.env
    export JSON_RPC_HASHKEY="https://your-archive-hashkey-rpc"
    poetry run python scripts/asseto/backfill-history.py

Useful environment variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``DRY_RUN``
     - If ``true``, print and validate the planned work without database writes.
   * - ``NETWORKS``
     - Optional comma-separated chain ids or names, e.g. ``177,hashkey``.
   * - ``PRODUCTS``
     - Optional comma-separated Asseto symbols, e.g. ``AoABT``.
   * - ``ASSETO_SCAN_PRICES``
     - If ``false``, update only metadata. Default: ``true``.
   * - ``ASSETO_CLEAN_PRICES``
     - If ``true``, replace only selected histories in cleaned prices. Default:
       ``true``.
   * - ``MAX_WORKERS``
     - Historical multicall worker count. Default: ``8``.
   * - ``FREQUENCY``
     - Historical price frequency, ``1h`` or ``1d``. Default: ``1d``.
   * - ``START_BLOCK`` / ``END_BLOCK``
     - Optional global scan range overrides.
   * - ``VAULT_DB_PATH`` / ``UNCLEANED_PRICE_DATABASE``
     - Optional production database path overrides.
   * - ``CLEANED_PRICE_DATABASE`` / ``READER_STATE_DATABASE``
     - Optional cleaned price and reader-state path overrides.

The backfill removes stale reader state only for selected Asseto vaults. This
is required because the targeted scanner replaces those rows from its explicit
start block onwards; retained later state would otherwise skip the rewrite.
"""

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.asseto.constants import ASSETO_PRODUCTS, HASHKEY_CHAIN_ID, AssetoProduct
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampDatabase
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Asseto's currently supported HashKey Chain RPC environment variable.
#:
#: Kept local to this script because the shared project chain registry does not
#: otherwise support HashKey Chain.
ASSETO_RPC_ENV_VAR = "JSON_RPC_HASHKEY"

#: Human-readable name for the currently supported Asseto deployment chain.
ASSETO_CHAIN_NAME = "HashKey Chain"


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value returned when the variable is unset.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_csv_env(name: str) -> set[str] | None:
    """Parse a comma-separated environment variable.

    :param name:
        Environment variable name.
    :return:
        Lowercase values, or ``None`` when unset.
    """

    value = os.environ.get(name, "").strip()
    return {part.strip().lower() for part in value.split(",") if part.strip()} if value else None


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable.

    :param name:
        Environment variable name.
    :return:
        Integer value, or ``None`` when unset.
    """

    value = os.environ.get(name)
    return int(value) if value else None


def parse_path_env(name: str, default: Path) -> Path:
    """Parse an optional filesystem path environment variable.

    :param name:
        Environment variable name.
    :param default:
        Default production path.
    :return:
        Expanded selected path.
    """

    return Path(os.environ[name]).expanduser() if os.environ.get(name) else default.expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Resolve the historical sampling frequency once for plan and scanner.

    :return:
        Daily sampling by default, or the explicit supported override.
    :raise ValueError:
        If ``FREQUENCY`` is not supported by the historical reader.
    """

    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")
    return cast(Literal["1h", "1d"], frequency)


def get_chain_selector_names(chain_id: int) -> set[str]:
    """Return accepted ``NETWORKS`` selector names for a chain.

    :param chain_id:
        EVM chain id.
    :return:
        Numeric and configured textual selector values.
    """

    if chain_id == HASHKEY_CHAIN_ID:
        return {str(chain_id), "hashkey", "hashkey chain"}
    return {str(chain_id)}


def get_asseto_rpc_env(chain_id: int) -> str:
    """Return the script-local RPC environment variable for Asseto.

    :param chain_id:
        Asseto product chain id.
    :return:
        ``JSON_RPC_HASHKEY`` for the currently supported HashKey deployment.
    :raise ValueError:
        If a future registry entry uses an unsupported chain.
    """

    if chain_id != HASHKEY_CHAIN_ID:
        message = f"Unsupported Asseto chain id: {chain_id}"
        raise ValueError(message)
    return ASSETO_RPC_ENV_VAR


def read_asseto_json_rpc_url(chain_id: int) -> str:
    """Read the script-local Asseto JSON-RPC URL from its environment variable.

    :param chain_id:
        Asseto product chain id.
    :return:
        Configured archive-capable HashKey Chain RPC URL.
    :raise ValueError:
        If the RPC variable is unset or the chain is unsupported.
    """

    rpc_env_var = get_asseto_rpc_env(chain_id)
    json_rpc_url = os.environ.get(rpc_env_var)
    if not json_rpc_url:
        message = f"Environment variable {rpc_env_var} is not set for Asseto chain {chain_id}"
        raise ValueError(message)
    return json_rpc_url


def iter_selected_products() -> Iterable[AssetoProduct]:
    """Iterate adapter-supported Asseto products filtered by environment.

    :return:
        Unique product metadata records selected by ``NETWORKS`` and
        ``PRODUCTS``.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    seen: set[tuple[int, HexAddress]] = set()
    for product in ASSETO_PRODUCTS.values():
        key = (product.chain_id, product.token)
        if key in seen:
            continue
        seen.add(key)
        if networks and not (get_chain_selector_names(product.chain_id) & networks):
            continue
        if products and product.symbol.lower() not in products:
            continue
        yield product


def resolve_price_scan_start_block(
    products: list[AssetoProduct],
    timestamp_cache_folder: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
) -> int:
    """Resolve a safe explicit history start for selected Asseto products.

    The normal scanner's incremental reader state must not decide the start of
    a targeted rewrite. Begin at the earliest Asseto deployment, unless a
    user supplied ``START_BLOCK`` or the local timestamp cache cannot serve
    that early block.

    :param products:
        Selected products on one EVM chain.
    :param timestamp_cache_folder:
        Directory containing per-chain timestamp cache databases.
    :return:
        Explicit, deployment, or timestamp-cache-supported first block.
    """

    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block

    assert products, "Cannot resolve a scan start block without Asseto products"
    chain_ids = {product.chain_id for product in products}
    assert len(chain_ids) == 1, f"Expected products from one chain, got {chain_ids}"
    deployment_start_block = min(product.first_seen_at_block for product in products)
    chain_id = products[0].chain_id
    cache_file = BlockTimestampDatabase.get_database_file_chain(chain_id, timestamp_cache_folder)
    if not cache_file.exists():
        return deployment_start_block

    timestamp_cache = BlockTimestampDatabase.load(chain_id, cache_file)
    try:
        first_cached_block = timestamp_cache.get_first_block()
    finally:
        timestamp_cache.close()

    if first_cached_block <= deployment_start_block:
        return deployment_start_block

    logger.warning(
        "Clipping Asseto history start on chain %d from deployment block %d to timestamp cache start block %d",
        chain_id,
        deployment_start_block,
        first_cached_block,
    )
    return first_cached_block


def create_asseto_detection(product: AssetoProduct) -> ERC4262VaultDetection:
    """Create a synthetic shared scanner detection for an Asseto product.

    :param product:
        Asseto product metadata.
    :return:
        Detection compatible with metadata scan record generation.
    """

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.asseto_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_asseto_lead(product: AssetoProduct) -> PotentialVaultMatch:
    """Create a synthetic discovery lead for an Asseto product.

    :param product:
        Asseto product metadata.
    :return:
        Lead compatible with the vault metadata database.
    """

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def read_vault_database(path: Path) -> VaultDatabase:
    """Read or initialise the vault metadata database.

    :param path:
        Vault metadata database path.
    :return:
        Existing or empty database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read trusted local historical reader states.

    :param path:
        Reader-state pickle file path.
    :return:
        Existing states, or an empty mapping when the file does not exist.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically write historical reader state.

    :param path:
        Reader-state pickle file path.
    :param states:
        Reader state mapping returned by the historical scanner.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def build_vaults(web3, products: list[AssetoProduct], token_cache: TokenDiskCache) -> list[VaultBase]:
    """Build Asseto vault adapters and attach deployment block hints.

    :param web3:
        Connected chain Web3 instance.
    :param products:
        Selected products on this chain.
    :param token_cache:
        Shared token metadata cache.
    :return:
        Configured Asseto vault adapters.
    """

    vaults: list[VaultBase] = []
    for product in products:
        vault = create_vault_instance(
            web3,
            product.token,
            features={ERC4626Feature.asseto_like},
            token_cache=token_cache,
        )
        if vault is None:
            raise RuntimeError(f"Could not create Asseto vault adapter for {product.symbol} {product.token}")
        vault.first_seen_at_block = product.first_seen_at_block
        vaults.append(vault)
    return vaults


def backfill_chain(
    chain_id: int,
    products: list[AssetoProduct],
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
    """Backfill one Asseto EVM chain.

    Metadata and historical writes use the same shared database and scanner
    paths as the normal vault pipeline, but both are limited to selected Asseto
    identifiers.

    :param chain_id:
        EVM chain id.
    :param products:
        Selected Asseto products on this chain.
    :param dry_run:
        Whether filesystem/database writes are disabled.
    :param scan_prices:
        Whether to scan supply, NAV, and TVL history.
    :param clean_prices:
        Whether to replace selected cleaned price histories.
    :param frequency:
        Historical sampling interval.
    :param vault_db:
        In-memory metadata database.
    :param vault_db_path:
        Metadata database output path.
    :param price_database_path:
        Uncleaned historical price parquet path.
    :param cleaned_price_database_path:
        Cleaned historical price parquet path.
    :param reader_state_database_path:
        Reader-state pickle path.
    :param token_cache:
        Shared token metadata cache.
    :return:
        Summary row for operator output.
    """

    rpc_env_var = get_asseto_rpc_env(chain_id)
    json_rpc_url = read_asseto_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_name = ASSETO_CHAIN_NAME
    logger.info("Backfilling %d Asseto products on %s using %s", len(products), chain_name, get_provider_name(web3.provider))

    end_block = parse_optional_int_env("END_BLOCK") or web3.eth.block_number
    leads = {product.token: create_asseto_lead(product) for product in products}
    rows = {
        VaultSpec(product.chain_id, product.token): create_vault_scan_record(
            web3,
            detection=create_asseto_detection(product),
            block_identifier=end_block,
            token_cache=token_cache,
        )
        for product in products
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

    scan_summary = "-"
    if scan_prices:
        vault_ids = {product.token.lower() for product in products}
        if dry_run:
            scan_summary = "dry-run"
        else:
            reader_states = read_reader_states(reader_state_database_path)
            reader_states = {spec: state for spec, state in reader_states.items() if spec.vault_address.lower() not in vault_ids}
            web3factory = MultiProviderWeb3Factory(json_rpc_url, retries=5)
            hypersync_config = configure_hypersync_from_env(web3)
            scan_result = scan_historical_prices_to_parquet(
                output_fname=price_database_path,
                web3=web3,
                web3factory=web3factory,
                vaults=build_vaults(web3, products, token_cache),
                start_block=resolve_price_scan_start_block(products),
                end_block=end_block,
                max_workers=int(os.environ.get("MAX_WORKERS", "8")),
                chunk_size=32,
                token_cache=token_cache,
                frequency=frequency,
                reader_states=reader_states,
                hypersync_client=hypersync_config.hypersync_client,
                vault_addresses=vault_ids,
            )
            write_reader_states(reader_state_database_path, scan_result["reader_states"])
            scan_summary = pformat_scan_result(scan_result)
            if clean_prices:
                cleaned_rows = replace_cleaned_vault_histories(
                    {VaultSpec(product.chain_id, product.token).as_string_id() for product in products},
                    vault_db_path=vault_db_path,
                    raw_price_df_path=price_database_path,
                    cleaned_price_df_path=cleaned_price_database_path,
                    logger=logger.info,
                )
                scan_summary = f"{scan_summary}; cleaned_rows={cleaned_rows:,}"

    return {
        "chain": chain_name,
        "chain_id": chain_id,
        "rpc": rpc_env_var,
        "products": ", ".join(product.symbol for product in products),
        "metadata_rows": len(rows),
        "scan": scan_summary,
    }


def main() -> None:
    """Run the targeted Asseto historical backfill."""

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/asseto-backfill-history.log"),
    )
    dry_run = parse_bool_env("DRY_RUN")
    scan_prices = parse_bool_env("ASSETO_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("ASSETO_CLEAN_PRICES", default=True)
    frequency = resolve_frequency()
    products = list(iter_selected_products())
    if not products:
        message = "No Asseto products selected"
        raise RuntimeError(message)

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_database_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    products_by_chain: dict[int, list[AssetoProduct]] = {}
    for product in products:
        products_by_chain.setdefault(product.chain_id, []).append(product)

    plan = [
        {
            "chain": ASSETO_CHAIN_NAME,
            "chain_id": chain_id,
            "rpc": get_asseto_rpc_env(chain_id),
            "products": ", ".join(product.symbol for product in chain_products),
            "first_block": min(product.first_seen_at_block for product in chain_products),
        }
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]
    print("Asseto backfill plan")
    print(tabulate(plan, headers="keys", tablefmt="github"))
    print(f"Vault DB: {vault_db_path}")
    print(f"Price DB: {price_database_path}")
    print(f"Cleaned price DB: {cleaned_price_database_path}")
    print(f"Reader states: {reader_state_database_path}")
    print(f"Frequency: {frequency}")
    print(f"Dry run: {dry_run}")
    print(f"Update cleaned prices: {clean_prices}")

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

    print("Asseto backfill summary")
    print(tabulate(summaries, headers="keys", tablefmt="github"))
    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.exception("Fatal error: %s", error, exc_info=error)
        sys.exit(1)
