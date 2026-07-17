#!/usr/bin/env python3
"""Backfill reviewed Ondo tokenised-fund leads and NAV history.

This is a targeted migration for the reviewed Ethereum USDY and OUSG tokens.
It upserts only those two leads and rewrites only their raw and cleaned price
histories. Existing vault database rows and reader states belonging to other
protocols are retained.

Run with ``source .local-test.env && poetry run python
scripts/ondo/backfill-history.py``. Set ``DRY_RUN=true`` to print the plan
without writes. ``ONDO_SCAN_PRICES=false`` updates metadata only;
``ONDO_CLEAN_PRICES=false`` skips the selected cleaned-history replacement.
``START_BLOCK``, ``END_BLOCK``, ``MAX_WORKERS``, ``FREQUENCY`` and the normal
vault database path environment variables use the same semantics as other
targeted vault migrations.
"""

# The operational entry point keeps its environment-backed parameters local.
# ruff: noqa: FBT001, PLR0914

import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.ondo.constants import ETHEREUM_CHAIN_ID, ONDO_PRODUCTS, OndoProduct
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase


def parse_bool_env(name: str, default: bool) -> bool:
    """Read a conventional boolean environment variable."""

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional database-path environment override."""

    return Path(os.environ[name]).expanduser() if name in os.environ else default.expanduser()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load existing reader state without discarding unrelated vaults."""

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically persist the complete post-scan reader-state mapping."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def create_detection(product: OndoProduct) -> ERC4262VaultDetection:
    """Create a scanner detection from the reviewed deployment registry."""

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.ondo_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_lead(product: OndoProduct) -> PotentialVaultMatch:
    """Create a hardcoded lead without re-discovering the whole chain."""

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def main() -> None:
    """Run the safe, address-scoped Ondo lead and price-history migration."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    products = tuple(ONDO_PRODUCTS.values())
    assert all(product.chain_id == ETHEREUM_CHAIN_ID for product in products)
    dry_run = parse_bool_env("DRY_RUN", False)
    scan_prices = parse_bool_env("ONDO_SCAN_PRICES", True)
    clean_prices = parse_bool_env("ONDO_CLEAN_PRICES", True)
    frequency = cast(Literal["1h", "1d"], os.environ.get("FREQUENCY", "1d"))
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"FREQUENCY must be 1h or 1d, got {frequency}")

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    web3 = create_multi_provider_web3(read_json_rpc_url(ETHEREUM_CHAIN_ID))
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    start_block = int(os.environ.get("START_BLOCK", min(product.first_seen_at_block for product in products)))
    plan = [{"product": product.product_name, "token": product.token, "start_block": start_block, "oracle_start": product.oracle_first_seen_at_block} for product in products]
    print(tabulate(plan, headers="keys", tablefmt="github"))

    token_cache = TokenDiskCache()
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    leads = {product.token: create_lead(product) for product in products}
    rows = {VaultSpec(product.chain_id, product.token): create_vault_scan_record(web3, create_detection(product), block_identifier=end_block, token_cache=token_cache) for product in products}
    if not dry_run:
        vault_db.update_leads_and_rows(chain_id=ETHEREUM_CHAIN_ID, last_scanned_block=end_block, leads=leads, rows=rows)
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.write(vault_db_path)

    if scan_prices and not dry_run:
        vault_ids = {product.token.lower() for product in products}
        states = {spec: state for spec, state in read_reader_states(reader_state_path).items() if spec.vault_address.lower() not in vault_ids}
        vaults = []
        for product in products:
            vault = create_vault_instance(web3, product.token, features={ERC4626Feature.ondo_like}, token_cache=token_cache)
            assert vault is not None
            vault.first_seen_at_block = product.first_seen_at_block
            vaults.append(vault)
        result = scan_historical_prices_to_parquet(
            output_fname=raw_price_path,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(read_json_rpc_url(ETHEREUM_CHAIN_ID), retries=5),
            vaults=vaults,
            start_block=start_block,
            end_block=end_block,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=states,
            hypersync_client=configure_hypersync_from_env(web3).hypersync_client,
            vault_addresses=vault_ids,
        )
        write_reader_states(reader_state_path, result["reader_states"])
        if clean_prices:
            replace_cleaned_vault_histories({VaultSpec(product.chain_id, product.token).as_string_id() for product in products}, vault_db_path=vault_db_path, raw_price_df_path=raw_price_path, cleaned_price_df_path=cleaned_price_path)
    if not dry_run:
        token_cache.commit()


if __name__ == "__main__":
    main()
