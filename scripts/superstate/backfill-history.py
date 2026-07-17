#!/usr/bin/env python3
"""Backfill the reviewed Superstate USTB lead and its NAV history.

This migration is intentionally address-scoped. It upserts only the Ethereum
USTB lead and metadata row, removes only that reader state, and passes only
USTB to the historical scanner. It never resets a chain cursor, re-discovers
all Ethereum vaults, or deletes unrelated Parquet rows.

Run with::

    source .local-test.env && poetry run python scripts/superstate/backfill-history.py

Environment variables:

``DRY_RUN``
    Show the migration plan without writing files. Default: ``false``.
``SCAN_PRICES``
    Rebuild USTB's daily NAV/share history. Default: ``true``.
``FREQUENCY``
    ``1d`` (default) or ``1h`` historical sampling interval.
``START_BLOCK`` / ``END_BLOCK``
    Optional inclusive bounds for a controlled repair.
``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE``
and ``READER_STATE_DATABASE``
    Optional state-path overrides for an isolated or production run.
"""

import os
import pickle  # noqa: S403 - trusted local reader-state pickle.
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
from eth_defi.tokenised_fund.superstate.constants import SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS, USTB_ETHEREUM_FIRST_SEEN_AT, USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read a conventional Boolean environment setting.

    :param name:
        Environment variable name.
    :param default:
        Value used when it is absent.
    :return:
        Parsed Boolean.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read a local state-path override.

    :param name:
        Environment variable name.
    :param default:
        Normal production location.
    :return:
        Expanded configured path.
    """

    return Path(os.environ.get(name, str(default))).expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Validate the requested historical sample interval.

    :return:
        Scanner frequency.
    :raises ValueError:
        If the requested frequency is unsupported.
    """

    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")
    return cast(Literal["1h", "1d"], frequency)


def create_detection() -> ERC4262VaultDetection:
    """Create the reviewed USTB scanner detection.

    :return:
        Hardcoded, non-event-derived USTB detection.
    """

    return ERC4262VaultDetection(
        chain=SUPERSTATE_ETHEREUM_CHAIN_ID,
        address=USTB_ETHEREUM_ADDRESS,
        first_seen_at_block=USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK,
        first_seen_at=USTB_ETHEREUM_FIRST_SEEN_AT,
        features={ERC4626Feature.superstate_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_lead() -> PotentialVaultMatch:
    """Create the USTB lead without scanning unrelated Ethereum events.

    :return:
        Reviewed USTB lead.
    """

    return PotentialVaultMatch(
        chain=SUPERSTATE_ETHEREUM_CHAIN_ID,
        address=USTB_ETHEREUM_ADDRESS,
        first_seen_at_block=USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK,
        first_seen_at=USTB_ETHEREUM_FIRST_SEEN_AT,
        deposit_count=0,
        withdrawal_count=0,
    )


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load trusted state while retaining every unrelated vault entry.

    :param path:
        Reader-state pickle location.
    :return:
        Current state map or an empty map.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, reader_states: dict[VaultSpec, dict]) -> None:
    """Atomically persist the complete retained reader-state map.

    :param path:
        Reader-state pickle location.
    :param reader_states:
        Complete state map from the scanner.
    :return:
        None.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(reader_states, out)


def main() -> None:  # noqa: PLR0914
    """Run the USTB-only lead and NAV-history migration.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=False)
    scan_prices = parse_bool_env("SCAN_PRICES", default=True)
    frequency = resolve_frequency()
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    uncleaned_price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    rpc_url = read_json_rpc_url(SUPERSTATE_ETHEREUM_CHAIN_ID)
    web3 = create_multi_provider_web3(rpc_url)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    start_block = int(os.environ.get("START_BLOCK", USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK))
    if start_block < USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK:
        message = f"START_BLOCK must not precede USTB deployment block {USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK}"
        raise ValueError(message)
    if start_block > end_block:
        message = "START_BLOCK must not exceed END_BLOCK"
        raise ValueError(message)

    plan = [{"chain": "Ethereum", "address": USTB_ETHEREUM_ADDRESS, "start_block": start_block, "end_block": end_block, "scan_prices": scan_prices, "dry_run": dry_run}]
    print(tabulate(plan, headers="keys", tablefmt="github"))
    token_cache = TokenDiskCache()
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    detection = create_detection()
    row = create_vault_scan_record(web3, detection=detection, block_identifier=end_block, token_cache=token_cache)
    spec = VaultSpec(SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS)

    if dry_run:
        print("DRY RUN: would upsert only USTB metadata and lead; no files changed")
        return

    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.update_leads_and_rows(
        chain_id=SUPERSTATE_ETHEREUM_CHAIN_ID,
        last_scanned_block=end_block,
        leads={USTB_ETHEREUM_ADDRESS: create_lead()},
        rows={spec: row},
    )
    vault_db.write(vault_db_path)

    if scan_prices:
        reader_states = read_reader_states(reader_state_path)
        reader_states.pop(spec, None)
        vault = create_vault_instance(web3, USTB_ETHEREUM_ADDRESS, features={ERC4626Feature.superstate_like}, token_cache=token_cache)
        if vault is None:
            message = "Could not create Superstate USTB adapter"
            raise RuntimeError(message)
        vault.first_seen_at_block = USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK
        hypersync_config = configure_hypersync_from_env(web3)
        result = scan_historical_prices_to_parquet(
            output_fname=uncleaned_price_path,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(rpc_url, retries=5),
            vaults=[vault],
            start_block=start_block,
            end_block=end_block,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
            vault_addresses={USTB_ETHEREUM_ADDRESS},
        )
        write_reader_states(reader_state_path, result["reader_states"])
        replace_cleaned_vault_histories({spec.as_string_id()}, vault_db_path=vault_db_path, raw_price_df_path=uncleaned_price_path, cleaned_price_df_path=cleaned_price_path)
        print(tabulate([{"historical_rows": result["rows_written_by_vault"].get(USTB_ETHEREUM_ADDRESS, 0), "price_rows": result["price_rows_written_by_vault"].get(USTB_ETHEREUM_ADDRESS, 0)}], headers="keys", tablefmt="github"))
    token_cache.commit()


if __name__ == "__main__":
    main()
