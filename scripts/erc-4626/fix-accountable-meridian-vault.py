"""Repair the Accountable Meridian Liquidity Provider vault classification.

This migration touches only the reviewed Robinhood Chain vault
``0x24b84023c8e4da635be228c380c09bfe5271bf9d``. It upserts one Accountable
metadata row and one discovery lead while preserving the Robinhood chain-wide
discovery cursor. Reader-state and Parquet files are left untouched unless
``ACCOUNTABLE_MERIDIAN_SCAN_PRICES=true`` is explicitly set.

Run metadata-only repair with::

    source .local-test.env && poetry run python scripts/erc-4626/fix-accountable-meridian-vault.py

Optional environment variables: ``DRY_RUN``, ``START_BLOCK``, ``END_BLOCK``,
``FREQUENCY``, ``MAX_WORKERS``, ``ACCOUNTABLE_MERIDIAN_SCAN_PRICES``,
``ACCOUNTABLE_MERIDIAN_CLEAN_PRICES``, ``VAULT_DB_PATH``,
``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE`` and
``READER_STATE_DATABASE``.
"""

import datetime
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from pathlib import Path
from typing import Literal, cast

from atomicwrites import atomic_write
from eth_typing import HexAddress

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
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

ROBINHOOD_CHAIN_ID = 4663
MERIDIAN_VAULT_ADDRESS = HexAddress("0x24b84023c8e4Da635be228C380C09bfE5271BF9d")
MERIDIAN_FIRST_SEEN_AT_BLOCK = 22_051_004
MERIDIAN_FIRST_SEEN_AT = datetime.datetime.fromtimestamp(1_785_287_703, tz=datetime.UTC).replace(tzinfo=None)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name: Environment variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed boolean value.
    """
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional path override.

    :param name: Environment variable name.
    :param default: Default production location.
    :return: Expanded selected path.
    """
    return Path(os.environ[name]).expanduser() if os.environ.get(name) else default.expanduser()


def resolve_frequency() -> Literal["1h", "1d"]:
    """Resolve the scanner sample frequency.

    :return: Hourly or daily scan frequency.
    :raise ValueError: If an unsupported frequency is requested.
    """
    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Accountable Meridian backfill supports FREQUENCY=1h or FREQUENCY=1d, got: {frequency}")
    return cast(Literal["1h", "1d"], frequency)


def resolve_start_block() -> int:
    """Choose the first block for an optional price rescan.

    :return: Explicit override or the vault creation block.
    """
    return int(os.environ["START_BLOCK"]) if os.environ.get("START_BLOCK") else MERIDIAN_FIRST_SEEN_AT_BLOCK


def selected_vault_spec() -> VaultSpec:
    """Return the only vault spec this migration may alter.

    :return: Meridian vault spec.
    """
    return VaultSpec(ROBINHOOD_CHAIN_ID, MERIDIAN_VAULT_ADDRESS)


def selected_vault_addresses() -> set[str]:
    """Return the only raw-history address this migration may replace.

    :return: Lower-case Meridian vault address set.
    """
    return {str(MERIDIAN_VAULT_ADDRESS).lower()}


def selected_vault_spec_ids() -> set[str]:
    """Return the only cleaned-history identifier this migration may replace.

    :return: Singleton Meridian vault spec id set.
    """
    return {selected_vault_spec().as_string_id()}


def create_meridian_detection() -> ERC4262VaultDetection:
    """Create the reviewed Accountable discovery record.

    :return: Scanner-compatible detection for the Meridian vault.
    """
    return ERC4262VaultDetection(
        chain=ROBINHOOD_CHAIN_ID,
        address=MERIDIAN_VAULT_ADDRESS,
        first_seen_at_block=MERIDIAN_FIRST_SEEN_AT_BLOCK,
        first_seen_at=MERIDIAN_FIRST_SEEN_AT,
        features={ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like, ERC4626Feature.accountable_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_meridian_lead() -> PotentialVaultMatch:
    """Create the one reviewed Meridian lead.

    :return: Meridian lead for the Robinhood Chain scanner database.
    """
    return PotentialVaultMatch(ROBINHOOD_CHAIN_ID, MERIDIAN_VAULT_ADDRESS, MERIDIAN_FIRST_SEEN_AT_BLOCK, MERIDIAN_FIRST_SEEN_AT)


def read_vault_database(path: Path) -> VaultDatabase:
    """Read existing metadata without discarding unrelated rows.

    :param path: Metadata pickle path.
    :return: Existing or empty database.
    """
    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def upsert_selected_metadata(vault_db: VaultDatabase, *, end_block: int, row: dict) -> None:
    """Upsert Meridian metadata without changing the Robinhood scan cursor.

    ``VaultDatabase.update_leads_and_rows()`` updates the ordinary chain-wide
    scan cursor. This migration proves one reviewed address only, so it restores
    the previous cursor after the row and lead have been merged.

    :param vault_db: Existing metadata database to update.
    :param end_block: Block used to build the metadata row.
    :param row: Meridian scan row.
    """
    previous_watermark = vault_db.last_scanned_block.get(ROBINHOOD_CHAIN_ID)
    spec = selected_vault_spec()
    vault_db.update_leads_and_rows(
        chain_id=ROBINHOOD_CHAIN_ID,
        last_scanned_block=end_block,
        leads={MERIDIAN_VAULT_ADDRESS: create_meridian_lead()},
        rows={spec: row},
    )
    if previous_watermark is None:
        del vault_db.last_scanned_block[ROBINHOOD_CHAIN_ID]
    else:
        vault_db.last_scanned_block[ROBINHOOD_CHAIN_ID] = previous_watermark


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Read trusted local reader states.

    :param path: Reader-state pickle path.
    :return: Complete state mapping, or empty mapping.
    """
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def remove_selected_reader_states(states: dict[VaultSpec, dict]) -> dict[VaultSpec, dict]:
    """Remove Meridian state while preserving every unrelated reader state.

    :param states: Existing complete reader-state mapping.
    :return: Mapping without the selected Meridian state.
    """
    selected = selected_vault_spec()
    return {spec: state for spec, state in states.items() if spec != selected}


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically persist the complete reader-state mapping.

    :param path: Reader-state pickle path.
    :param states: Complete state mapping.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as output:
        pickle.dump(states, output)


def run_migration(
    *,
    dry_run: bool,
    scan_prices: bool,
    clean_prices: bool,
    frequency: Literal["1h", "1d"],
    vault_db_path: Path,
    raw_price_path: Path,
    cleaned_price_path: Path,
    reader_state_path: Path,
) -> None:
    """Run the address-scoped Accountable Meridian migration.

    :param dry_run: Do not write metadata, reader states or Parquet rows.
    :param scan_prices: Rebuild Meridian raw history.
    :param clean_prices: Replace only Meridian cleaned history after raw scan.
    :param frequency: Shared scanner sample frequency.
    :param vault_db_path: Metadata database path.
    :param raw_price_path: Raw price Parquet path.
    :param cleaned_price_path: Cleaned price Parquet path.
    :param reader_state_path: Reader-state pickle path.
    """
    json_rpc_url = read_json_rpc_url(ROBINHOOD_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    start_block = resolve_start_block()
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else web3.eth.block_number
    if end_block < start_block:
        raise ValueError(f"END_BLOCK {end_block} is before Meridian deployment block {start_block}")

    token_cache = TokenDiskCache()
    detection = create_meridian_detection()
    row = create_vault_scan_record(web3, detection=detection, block_identifier=end_block, token_cache=token_cache)

    logger.info("Accountable Meridian repair: %s blocks %d..%d; dry-run=%s; scan-prices=%s", MERIDIAN_VAULT_ADDRESS, start_block, end_block, dry_run, scan_prices)
    logger.info("Vault DB: %s", vault_db_path)
    logger.info("Raw prices: %s", raw_price_path)
    logger.info("Cleaned prices: %s", cleaned_price_path)

    if not dry_run:
        vault_db = read_vault_database(vault_db_path)
        upsert_selected_metadata(vault_db, end_block=end_block, row=row)
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.write(vault_db_path)
        token_cache.commit()

    if dry_run or not scan_prices:
        return

    vault = create_vault_instance(web3, MERIDIAN_VAULT_ADDRESS, features=detection.features, token_cache=token_cache)
    if vault is None:
        message = "Could not create Accountable Meridian adapter"
        raise RuntimeError(message)
    vault.first_seen_at_block = MERIDIAN_FIRST_SEEN_AT_BLOCK

    states = remove_selected_reader_states(read_reader_states(reader_state_path))
    hypersync_config = configure_hypersync_from_env(web3)
    if hypersync_config.hypersync_client is None:
        message = "Accountable Meridian price backfill requires a configured HyperSync client"
        raise RuntimeError(message)
    result = scan_historical_prices_to_parquet(
        output_fname=raw_price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
        vaults=[vault],
        start_block=start_block,
        end_block=end_block,
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        chunk_size=32,
        token_cache=token_cache,
        frequency=frequency,
        reader_states=states,
        hypersync_client=hypersync_config.hypersync_client,
        vault_addresses=selected_vault_addresses(),
    )
    write_reader_states(reader_state_path, result["reader_states"])
    if clean_prices:
        replace_cleaned_vault_histories(selected_vault_spec_ids(), vault_db_path=vault_db_path, raw_price_df_path=raw_price_path, cleaned_price_df_path=cleaned_price_path, logger=logger.info)
    token_cache.commit()
    logger.info("%s", pformat_scan_result(result))


def main() -> None:
    """Read configuration and execute the scoped migration."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/accountable-meridian-vault-repair.log"))
    run_migration(
        dry_run=parse_bool_env("DRY_RUN"),
        scan_prices=parse_bool_env("ACCOUNTABLE_MERIDIAN_SCAN_PRICES", default=False),
        clean_prices=parse_bool_env("ACCOUNTABLE_MERIDIAN_CLEAN_PRICES", default=True),
        frequency=resolve_frequency(),
        vault_db_path=parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE),
        raw_price_path=parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE),
        cleaned_price_path=parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE),
        reader_state_path=parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE),
    )


if __name__ == "__main__":
    main()
