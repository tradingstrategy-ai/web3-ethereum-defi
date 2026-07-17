#!/usr/bin/env python3
"""Backfill reviewed Franklin Templeton Benji Ethereum fund-token history.

The migration only touches the two addresses in ``FRANKLIN_PRODUCTS``. It
retains unrelated vault metadata, raw and cleaned Parquet rows, and reader
states. The targeted scanner deletes and regenerates history for those token
addresses from their known deployment blocks, so it must also remove only
their saved reader states before scanning.

Run with::

    source .local-test.env && poetry run python scripts/franklin/backfill-history.py

Set ``DRY_RUN=false`` to apply changes. Optional environment variables are
``FRANKLIN_SCAN_PRICES`` (default ``true``), ``FRANKLIN_CLEAN_PRICES``
(default ``true``), ``FREQUENCY`` (``1h`` or ``1d``), ``START_BLOCK``,
``END_BLOCK``, ``MAX_WORKERS``, ``VAULT_DB_PATH``,
``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE`` and
``READER_STATE_DATABASE``.
"""

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from pathlib import Path

from atomicwrites import atomic_write
from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.franklin.constants import ETHEREUM_CHAIN_ID, FRANKLIN_PRODUCTS, FranklinProduct
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse an environment boolean.

    :param name:
        Environment variable name.
    :param default:
        Fallback value when unset.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional path override.

    :param name:
        Environment variable name.
    :param default:
        Production default path.
    :return:
        Selected path.
    """

    return Path(os.environ[name]).expanduser() if os.environ.get(name) else default


def create_detection(product: FranklinProduct) -> ERC4262VaultDetection:
    """Create a shared scanner detection for a registered product.

    :param product:
        Reviewed Benji Ethereum product.
    :return:
        Hardcoded feature detection.
    """

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.franklin_like},
        updated_at=product.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )


def create_lead(product: FranklinProduct) -> PotentialVaultMatch:
    """Create a non-ERC-4626 hardcoded lead.

    :param product:
        Reviewed Benji Ethereum product.
    :return:
        Shared discovery lead.
    """

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def upsert_franklin_metadata_preserving_discovery_cursor(
    vault_db: VaultDatabase,
    leads: dict[HexAddress, PotentialVaultMatch],
    rows: dict[VaultSpec, VaultRow],
) -> None:
    """Upsert reviewed Benji metadata without changing discovery state.

    A targeted migration must preserve both an existing Ethereum discovery
    cursor and the absence of one. Advancing or initialising that chain-wide
    cursor could skip unrelated contracts that have not yet been discovered.

    :param vault_db:
        Existing vault metadata database.
    :param leads:
        Reviewed Benji leads keyed by token address.
    :param rows:
        Fresh Benji scan rows keyed by :class:`VaultSpec`.
    :return:
        None.
    """

    vault_db.leads.update({VaultSpec(ETHEREUM_CHAIN_ID, address): lead for address, lead in leads.items()})
    vault_db._merge_rows(rows)


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load saved reader states without altering unrelated entries.

    :param path:
        Reader-state pickle path.
    :return:
        Complete existing mapping, or an empty mapping.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Write complete reader state atomically.

    :param path:
        Reader-state pickle destination.
    :param states:
        Complete scanner-produced state mapping.
    :return:
        None.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def main() -> None:  # noqa: PLR0914
    """Run a scoped Benji metadata and historical-price migration.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/franklin-backfill-history.log"))
    products = tuple(FRANKLIN_PRODUCTS.values())
    if any(product.chain_id != ETHEREUM_CHAIN_ID for product in products):
        message = "Franklin backfill supports reviewed Ethereum products only"
        raise RuntimeError(message)

    dry_run = parse_bool_env("DRY_RUN", default=True)
    scan_prices = parse_bool_env("FRANKLIN_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("FRANKLIN_CLEAN_PRICES", default=True)
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        message = "FREQUENCY must be 1h or 1d"
        raise ValueError(message)
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_database_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    json_rpc_url = read_json_rpc_url(ETHEREUM_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else web3.eth.block_number
    start_block = int(os.environ["START_BLOCK"]) if os.environ.get("START_BLOCK") else min(product.first_seen_at_block for product in products)
    token_cache = TokenDiskCache()

    plan = [{"symbol": product.symbol, "token": product.token, "start_block": start_block, "end_block": end_block} for product in products]
    print("Franklin Templeton Benji backfill plan")
    print(tabulate(plan, headers="keys", tablefmt="github"))
    if dry_run:
        return

    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    leads = {product.token: create_lead(product) for product in products}
    rows = {VaultSpec(product.chain_id, product.token): create_vault_scan_record(web3, create_detection(product), block_identifier=end_block, token_cache=token_cache) for product in products}
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_franklin_metadata_preserving_discovery_cursor(vault_db, leads, rows)
    vault_db.write(vault_db_path)

    scan_summary = "disabled"
    if scan_prices:
        vault_ids = {product.token.lower() for product in products}
        reader_states = {spec: state for spec, state in read_reader_states(reader_state_database_path).items() if spec.vault_address.lower() not in vault_ids}
        vaults = []
        for product in products:
            vault = create_vault_instance(web3, product.token, features={ERC4626Feature.franklin_like}, token_cache=token_cache)
            if vault is None:
                raise RuntimeError(f"Could not create Franklin adapter for {product.token}")
            vault.first_seen_at_block = product.first_seen_at_block
            vaults.append(vault)
        hypersync_config = configure_hypersync_from_env(web3)
        result = scan_historical_prices_to_parquet(
            output_fname=price_database_path,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
            vaults=vaults,
            start_block=start_block,
            end_block=end_block,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,  # type: ignore[arg-type]
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
            vault_addresses=vault_ids,
        )
        write_reader_states(reader_state_database_path, result["reader_states"])
        scan_summary = pformat_scan_result(result)
        if clean_prices:
            changed_rows = replace_cleaned_vault_histories(
                {VaultSpec(product.chain_id, product.token).as_string_id() for product in products},
                vault_db_path=vault_db_path,
                raw_price_df_path=price_database_path,
                cleaned_price_df_path=cleaned_price_database_path,
                logger=logger.info,
            )
            scan_summary = f"{scan_summary}; cleaned_rows={changed_rows:,}"
    token_cache.commit()
    print(tabulate([{"chain": get_chain_name(ETHEREUM_CHAIN_ID), "products": len(products), "scan": scan_summary}], headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()
