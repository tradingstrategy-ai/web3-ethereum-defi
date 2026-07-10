#!/usr/bin/env python3
"""Backfill historical Midas vault data into the shared vault pipeline.

This script is a targeted production backfill tool for Midas products supported
by the :mod:`eth_defi.midas` adapter. It avoids whole-chain rediscovery and only
touches Midas vault ids generated from the Pythonised registry into
:data:`eth_defi.midas.constants.MIDAS_PRODUCTS`.

The script:

1. Upserts Midas product leads into the vault metadata database.
2. Upserts Midas metadata rows through the normal ``VaultBase`` scan-record path.
3. Scans historical share price and TVL rows only for selected Midas vault ids.

Historical price writes are scoped by ``vault_addresses``. Existing rows for
other vaults are preserved.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/backfill-history.py

Useful environment variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``DRY_RUN``
     - If ``true``, only print planned work. Default: ``false``.
   * - ``NETWORKS``
     - Optional comma-separated chain ids or chain names, e.g. ``1,ethereum``.
   * - ``PRODUCTS``
     - Optional comma-separated Midas product symbols, e.g. ``mTBILL,mBASIS``.
   * - ``MIDAS_SCAN_PRICES``
     - If ``false``, update only leads and metadata. Default: ``true``.
   * - ``MIDAS_REWRITE_TARGETED``
     - If ``true``, clear reader states for selected Midas vaults so history is
       rewritten from their first deployment block. Default: ``true``.
   * - ``MAX_WORKERS``
     - Historical multicall worker count. Default: ``8``.
   * - ``FREQUENCY``
     - Historical price frequency, ``1h`` or ``1d``. Default: ``1h``.
   * - ``START_BLOCK``
     - Optional global minimum start block override.
   * - ``END_BLOCK``
     - Optional global end block override.
   * - ``VAULT_DB_PATH``
     - Optional metadata DB path. Default: production vault metadata DB.
   * - ``UNCLEANED_PRICE_DATABASE``
     - Optional uncleaned price parquet path. Default: production price DB.
   * - ``READER_STATE_DATABASE``
     - Optional reader-state pickle path. Default: production reader state DB.

JSON-RPC URLs are read per chain using the normal ``JSON_RPC_<CHAIN_NAME>``
environment variables, e.g. ``JSON_RPC_ETHEREUM``.
"""

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from collections.abc import Iterable
from pathlib import Path

from atomicwrites import atomic_write
from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.chain import CHAIN_NAMES, get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.midas.constants import MIDAS_PRODUCTS, MidasProduct
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


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
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_csv_env(name: str) -> set[str] | None:
    """Parse a comma-separated environment variable.

    :param name:
        Environment variable name.
    :return:
        Lowercase values or ``None`` when unset.
    """

    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable.

    :param name:
        Environment variable name.
    :return:
        Integer value or ``None`` when unset.
    """

    value = os.environ.get(name)
    if not value:
        return None
    return int(value)


def parse_path_env(name: str, default: Path) -> Path:
    """Parse a path environment variable.

    :param name:
        Environment variable name.
    :param default:
        Default path.
    :return:
        Expanded path.
    """

    value = os.environ.get(name)
    if value:
        return Path(value).expanduser()
    return default.expanduser()


def setup_logging() -> None:
    """Set up console and file logging."""

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/midas-backfill-history.log"),
    )


def get_chain_selector_names(chain_id: int) -> set[str]:
    """Return accepted selector values for a chain.

    :param chain_id:
        EVM chain id.
    :return:
        Lowercase selectors accepted by ``NETWORKS``.
    """

    selectors = {str(chain_id)}
    chain_name = CHAIN_NAMES.get(chain_id)
    if chain_name:
        selectors.add(chain_name.lower())
    return selectors


def iter_selected_products() -> Iterable[MidasProduct]:
    """Iterate selected adapter-supported Midas products.

    :return:
        Midas products filtered by ``NETWORKS`` and ``PRODUCTS``.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")

    seen: set[tuple[int, HexAddress]] = set()
    for product in MIDAS_PRODUCTS.values():
        key = (product.chain_id, product.token)
        if key in seen:
            continue
        seen.add(key)

        if networks and not (get_chain_selector_names(product.chain_id) & networks):
            continue
        if products and product.symbol.lower() not in products:
            continue
        yield product


def create_midas_detection(product: MidasProduct) -> ERC4262VaultDetection:
    """Create a synthetic detection row for a Midas product.

    :param product:
        Midas product metadata.
    :return:
        Detection data compatible with the shared vault pipeline.
    """

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.midas_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_midas_lead(product: MidasProduct) -> PotentialVaultMatch:
    """Create a synthetic lead row for a Midas product.

    :param product:
        Midas product metadata.
    :return:
        Lead data compatible with the shared vault pipeline.
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
    """Read or initialise a vault metadata database.

    :param path:
        Vault database path.
    :return:
        Existing or empty vault database.
    """

    if path.exists():
        return VaultDatabase.read(path)
    return VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read historical reader states.

    :param path:
        Reader-state pickle path.
    :return:
        Reader states, or an empty dictionary when missing.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Write historical reader states atomically.

    :param path:
        Reader-state pickle path.
    :param states:
        Reader states to write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def build_vaults(web3, products: list[MidasProduct], token_cache: TokenDiskCache) -> list[VaultBase]:
    """Build Midas vault adapter instances.

    :param web3:
        Web3 connection.
    :param products:
        Midas products on the connected chain.
    :param token_cache:
        Shared token cache.
    :return:
        Vault adapter instances with ``first_seen_at_block`` hints.
    """

    vaults: list[VaultBase] = []
    for product in products:
        vault = create_vault_instance(
            web3,
            product.token,
            features={ERC4626Feature.midas_like},
            token_cache=token_cache,
        )
        if vault is None:
            message = f"Could not create Midas vault adapter for {product.symbol} {product.token}"
            raise RuntimeError(message)
        vault.first_seen_at_block = product.first_seen_at_block
        vaults.append(vault)
    return vaults


def backfill_chain(  # noqa: PLR0914
    chain_id: int,
    products: list[MidasProduct],
    *,
    dry_run: bool,
    scan_prices: bool,
    rewrite_targeted: bool,
    vault_db: VaultDatabase,
    vault_db_path: Path,
    price_database_path: Path,
    reader_state_database_path: Path,
    token_cache: TokenDiskCache,
) -> dict[str, object]:
    """Backfill one Midas chain.

    :param chain_id:
        EVM chain id.
    :param products:
        Midas products on this chain.
    :param dry_run:
        Whether writes are disabled.
    :param scan_prices:
        Whether to scan historical prices.
    :param rewrite_targeted:
        Whether to clear selected vault reader states before scanning.
    :param vault_db:
        Vault metadata database.
    :param vault_db_path:
        Vault metadata database path.
    :param price_database_path:
        Historical price parquet path.
    :param reader_state_database_path:
        Reader-state pickle path.
    :param token_cache:
        Shared token cache.
    :return:
        Summary row for tabular output.
    """

    rpc_env_var = get_json_rpc_env(chain_id)
    json_rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    chain_name = get_chain_name(chain_id)
    logger.info("Backfilling %d Midas products on %s using %s", len(products), chain_name, get_provider_name(web3.provider))

    end_block = parse_optional_int_env("END_BLOCK") or web3.eth.block_number
    metadata_block = end_block
    leads = {product.token: create_midas_lead(product) for product in products}
    rows = {}

    for product in products:
        detection = create_midas_detection(product)
        rows[VaultSpec(product.chain_id, product.token)] = create_vault_scan_record(
            web3,
            detection=detection,
            block_identifier=metadata_block,
            token_cache=token_cache,
        )

    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.update_leads_and_rows(
            chain_id=chain_id,
            last_scanned_block=metadata_block,
            leads=leads,
            rows=rows,
        )
        vault_db.write(vault_db_path)
        logger.info("Updated %s with %d Midas metadata rows", vault_db_path, len(rows))

    scan_summary = "-"
    if scan_prices:
        vault_ids = {product.token.lower() for product in products}
        if dry_run:
            scan_summary = "dry-run"
        else:
            reader_states = read_reader_states(reader_state_database_path)
            if rewrite_targeted:
                reader_states = {spec: state for spec, state in reader_states.items() if spec.vault_address.lower() not in vault_ids}

            web3factory = MultiProviderWeb3Factory(json_rpc_url, retries=5)
            hypersync_config = configure_hypersync_from_env(web3)
            vaults = build_vaults(web3, products, token_cache)
            scan_result = scan_historical_prices_to_parquet(
                output_fname=price_database_path,
                web3=web3,
                web3factory=web3factory,
                vaults=vaults,
                start_block=parse_optional_int_env("START_BLOCK"),
                end_block=end_block,
                max_workers=int(os.environ.get("MAX_WORKERS", "8")),
                chunk_size=32,
                token_cache=token_cache,
                frequency=os.environ.get("FREQUENCY", "1h"),
                reader_states=reader_states,
                hypersync_client=hypersync_config.hypersync_client,
                vault_addresses=vault_ids,
            )
            write_reader_states(reader_state_database_path, scan_result["reader_states"])
            scan_summary = pformat_scan_result(scan_result)

    return {
        "chain": chain_name,
        "chain_id": chain_id,
        "rpc": rpc_env_var,
        "products": ", ".join(product.symbol for product in products),
        "metadata_rows": len(rows),
        "scan": scan_summary,
    }


def main() -> None:
    """Run the Midas backfill."""

    setup_logging()

    dry_run = parse_bool_env("DRY_RUN")
    scan_prices = parse_bool_env("MIDAS_SCAN_PRICES", default=True)
    rewrite_targeted = parse_bool_env("MIDAS_REWRITE_TARGETED", default=True)
    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        message = f"Unsupported FREQUENCY: {frequency}"
        raise ValueError(message)

    products = list(iter_selected_products())
    if not products:
        message = "No Midas products selected"
        raise RuntimeError(message)

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)

    products_by_chain: dict[int, list[MidasProduct]] = {}
    for product in products:
        products_by_chain.setdefault(product.chain_id, []).append(product)

    plan = [
        {
            "chain": get_chain_name(chain_id),
            "chain_id": chain_id,
            "rpc": get_json_rpc_env(chain_id),
            "products": ", ".join(product.symbol for product in chain_products),
            "first_block": min(product.first_seen_at_block for product in chain_products),
        }
        for chain_id, chain_products in sorted(products_by_chain.items())
    ]

    print("Midas backfill plan")
    print(tabulate(plan, headers="keys", tablefmt="github"))
    print(f"Vault DB: {vault_db_path}")
    print(f"Price DB: {price_database_path}")
    print(f"Reader states: {reader_state_database_path}")
    print(f"Frequency: {frequency}")
    print(f"Dry run: {dry_run}")

    vault_db = read_vault_database(vault_db_path)
    token_cache = TokenDiskCache()

    summaries = []
    for chain_id, chain_products in sorted(products_by_chain.items()):
        summaries.append(
            backfill_chain(
                chain_id,
                chain_products,
                dry_run=dry_run,
                scan_prices=scan_prices,
                rewrite_targeted=rewrite_targeted,
                vault_db=vault_db,
                vault_db_path=vault_db_path,
                price_database_path=price_database_path,
                reader_state_database_path=reader_state_database_path,
                token_cache=token_cache,
            )
        )

    if not dry_run:
        token_cache.commit()

    print("Midas backfill summary")
    print(tabulate(summaries, headers="keys", tablefmt="github"))
    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        sys.exit(1)
