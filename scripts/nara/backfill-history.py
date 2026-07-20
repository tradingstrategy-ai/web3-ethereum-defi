#!/usr/bin/env python3
# ruff: noqa: PLR0914
"""Backfill NaraUSD+ history into the shared vault pipeline.

This migration only updates NaraUSD+ on Ethereum. It preserves unrelated vault
metadata, reader-state entries and raw or cleaned price histories.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/nara/backfill-history.py

Environment variables:

- ``DRY_RUN``: Report planned work without writing data. Defaults to ``false``.
- ``NARA_SCAN_PRICES``: Scan NaraUSD+ price history. Defaults to ``true``.
- ``NARA_CLEAN_PRICES``: Rebuild only NaraUSD+ cleaned history. Defaults to ``true``.
- ``FREQUENCY``: Historical price frequency, ``1h`` or ``1d``. Defaults to ``1h``.
- ``MAX_WORKERS``: Historical multicall worker count. Defaults to ``8``.
- ``END_BLOCK``: Optional Ethereum end block override.
- ``VAULT_DB_PATH``: Vault metadata database path.
- ``UNCLEANED_PRICE_DATABASE``: Raw vault price Parquet path.
- ``CLEANED_PRICE_DATABASE``: Cleaned vault price Parquet path.
- ``READER_STATE_DATABASE``: Vault reader-state pickle path.
"""

import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.nara.constants import NARA_CHAIN_ID, NARAUSD_PLUS_FIRST_SEEN_AT, NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK, NARAUSD_PLUS_VAULT
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value when the variable is unset.
    :return:
        Parsed boolean value.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Resolve a filesystem path from an environment variable.

    :param name:
        Environment variable name.
    :param default:
        Default path when the variable is unset.
    :return:
        Expanded configured path.
    """
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default.expanduser()


def read_vault_database(path: Path) -> VaultDatabase:
    """Read a vault database or create an empty one.

    :param path:
        Vault database pickle path.
    :return:
        Existing or empty vault database.
    """
    if path.exists():
        return VaultDatabase.read(path)
    return VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read persisted historical reader states.

    :param path:
        Reader-state pickle path.
    :return:
        Persisted reader states, or an empty mapping.
    """
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Write reader states atomically.

    :param path:
        Reader-state pickle path.
    :param states:
        Reader states to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def create_nara_detection() -> ERC4262VaultDetection:
    """Create the synthetic detection row for the NaraUSD+ hardcoded vault.

    :return:
        Detection record compatible with the shared vault metadata pipeline.
    """
    return ERC4262VaultDetection(
        chain=NARA_CHAIN_ID,
        address=NARAUSD_PLUS_VAULT,
        first_seen_at_block=NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK,
        first_seen_at=NARAUSD_PLUS_FIRST_SEEN_AT,
        features={ERC4626Feature.nara_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_nara_lead() -> PotentialVaultMatch:
    """Create the NaraUSD+ hardcoded discovery lead.

    :return:
        Lead data compatible with the shared vault discovery pipeline.
    """
    return PotentialVaultMatch(
        chain=NARA_CHAIN_ID,
        address=NARAUSD_PLUS_VAULT,
        first_seen_at_block=NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK,
        first_seen_at=NARAUSD_PLUS_FIRST_SEEN_AT,
        deposit_count=0,
        withdrawal_count=0,
    )


def resolve_frequency() -> Literal["1h", "1d"]:
    """Read the requested historical sampling frequency.

    :return:
        Validated historical sampling frequency.
    :raise ValueError:
        If ``FREQUENCY`` is unsupported.
    """
    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")
    return cast(Literal["1h", "1d"], frequency)


def main() -> None:
    """Run the scoped NaraUSD+ metadata and price-history backfill."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/nara-backfill-history.log"))
    dry_run = parse_bool_env("DRY_RUN", default=False)
    scan_prices = parse_bool_env("NARA_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("NARA_CLEAN_PRICES", default=True)
    frequency = resolve_frequency()
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_database_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_database_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_database_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    json_rpc_url = read_json_rpc_url(NARA_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))

    plan = [{"chain": "Ethereum", "vault": NARAUSD_PLUS_VAULT, "first_block": NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK, "end_block": end_block, "scan_prices": scan_prices, "dry_run": dry_run}]
    print(tabulate(plan, headers="keys", tablefmt="github"))

    token_cache = TokenDiskCache()
    vault_db = VaultDatabase() if dry_run else read_vault_database(vault_db_path)
    detection = create_nara_detection()
    row = create_vault_scan_record(web3, detection=detection, block_identifier=end_block, token_cache=token_cache)
    spec = VaultSpec(NARA_CHAIN_ID, NARAUSD_PLUS_VAULT)
    if not dry_run:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.update_leads_and_rows(
            chain_id=NARA_CHAIN_ID,
            last_scanned_block=end_block,
            leads={NARAUSD_PLUS_VAULT: create_nara_lead()},
            rows={spec: row},
        )
        vault_db.write(vault_db_path)

    if scan_prices and not dry_run:
        vault = create_vault_instance(web3, NARAUSD_PLUS_VAULT, features={ERC4626Feature.nara_like}, token_cache=token_cache)
        if vault is None:
            message = "Could not create NaraUSD+ vault adapter"
            raise RuntimeError(message)
        vault.first_seen_at_block = NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK
        reader_states = read_reader_states(reader_state_database_path)
        reader_states = {saved_spec: state for saved_spec, state in reader_states.items() if saved_spec != spec}
        web3factory = MultiProviderWeb3Factory(json_rpc_url, retries=5)
        hypersync_config = configure_hypersync_from_env(web3)
        scan_result = scan_historical_prices_to_parquet(
            output_fname=price_database_path,
            web3=web3,
            web3factory=web3factory,
            vaults=[vault],
            start_block=NARAUSD_PLUS_FIRST_SEEN_AT_BLOCK,
            end_block=end_block,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
            vault_addresses={NARAUSD_PLUS_VAULT},
        )
        write_reader_states(reader_state_database_path, scan_result["reader_states"])
        logger.info("NaraUSD+ historical scan: %s", pformat_scan_result(scan_result))
        if clean_prices:
            cleaned_rows = replace_cleaned_vault_histories(
                {spec.as_string_id()},
                vault_db_path=vault_db_path,
                raw_price_df_path=price_database_path,
                cleaned_price_df_path=cleaned_price_database_path,
                logger=logger.info,
            )
            logger.info("Rebuilt %d NaraUSD+ cleaned price rows", cleaned_rows)

    if not dry_run:
        token_cache.commit()
    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.exception("Fatal error: %s", error, exc_info=error)
        sys.exit(1)
