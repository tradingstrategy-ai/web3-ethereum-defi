"""Backfill only WisdomTree WTGXX metadata and price history.

This migration never removes an Ethereum-wide scan range. It upserts only the
WTGXX lead and metadata row, removes only its reader state before scanning, and
passes WTGXX as ``vault_addresses`` to the shared Parquet scanner. Cleaned
history replacement is likewise restricted to its one ``VaultSpec`` id.

Run with::

    source .local-test.env && WISDOMTREE_DATASPAN_API_KEY=... PROTOCOLS=wisdomtree poetry run python scripts/backfill-tokenised-funds.py
"""

import datetime
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
from pathlib import Path
from typing import Literal

from atomicwrites import atomic_write

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
from eth_defi.tokenised_fund.wisdomtree.constants import WTGXX_ETHEREUM
from eth_defi.tokenised_fund.wisdomtree.nav import WISDOMTREE_DATASPAN_API_KEY_ENV
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment value.

    :param name: Environment variable name.
    :param default: Value when the variable is absent.
    :return: Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def parse_path_env(name: str, default: Path) -> Path:
    """Read an optional output-path override.

    :param name: Environment variable name.
    :param default: Production default path.
    :return: Resolved configured path.
    """

    return Path(os.environ[name]).expanduser() if name in os.environ else default.expanduser()


def selected_vault_addresses() -> set[str]:
    """Return the only addresses this migration may alter.

    :return: Singleton lower-case WTGXX address set.
    """

    return {WTGXX_ETHEREUM.token.lower()}


def selected_vault_spec_ids() -> set[str]:
    """Return the only cleaned-history ids this migration may replace.

    :return: Singleton WTGXX vault spec id set.
    """

    return {VaultSpec(WTGXX_ETHEREUM.chain_id, WTGXX_ETHEREUM.token).as_string_id()}


def require_price_scan_key() -> None:
    """Fail closed before history writes if issuer NAV cannot be authenticated.

    :raise RuntimeError: If the documented DataSpan API key is missing.
    """

    if not os.environ.get(WISDOMTREE_DATASPAN_API_KEY_ENV):
        message = f"{WISDOMTREE_DATASPAN_API_KEY_ENV} is required for the WTGXX price-history scan"
        raise RuntimeError(message)


def read_vault_database(path: Path) -> VaultDatabase:
    """Read existing metadata without discarding unrelated rows.

    :param path: Metadata pickle path.
    :return: Existing or empty database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


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
    """Remove WTGXX state while preserving every unrelated reader state.

    :param states: Existing complete reader-state mapping.
    :return: Mapping without the selected WTGXX state.
    """

    selected_spec = VaultSpec(WTGXX_ETHEREUM.chain_id, WTGXX_ETHEREUM.token)
    return {spec: state for spec, state in states.items() if spec != selected_spec}


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Atomically persist the complete preserved state mapping.

    :param path: Reader-state pickle path.
    :param states: Complete state mapping.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as output:
        pickle.dump(states, output)


def create_detection(deployment_block: int, first_seen_at: datetime.datetime) -> ERC4262VaultDetection:
    """Create WTGXX routing data without an Ethereum-wide event replay.

    :param deployment_block: First block with WTGXX proxy code.
    :param first_seen_at: Corresponding naive UTC timestamp.
    :return: Scanner-compatible detection.
    """

    return ERC4262VaultDetection(
        chain=WTGXX_ETHEREUM.chain_id,
        address=WTGXX_ETHEREUM.token,
        first_seen_at_block=deployment_block,
        first_seen_at=first_seen_at,
        features={ERC4626Feature.wisdomtree_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_lead(deployment_block: int, first_seen_at: datetime.datetime) -> PotentialVaultMatch:
    """Create the one reviewed WTGXX lead.

    :param deployment_block: First block with WTGXX proxy code.
    :param first_seen_at: Corresponding naive UTC timestamp.
    :return: WTGXX lead.
    """

    return PotentialVaultMatch(WTGXX_ETHEREUM.chain_id, WTGXX_ETHEREUM.token, deployment_block, first_seen_at)


def upsert_selected_metadata(vault_db: VaultDatabase, *, end_block: int, row: dict) -> None:
    """Upsert WTGXX metadata without changing the Ethereum discovery watermark.

    ``VaultDatabase.update_leads_and_rows()`` also maintains the ordinary
    chain-wide scan cursor. A one-address migration must leave that cursor
    untouched because it says nothing about unrelated Ethereum discovery.

    :param vault_db: Existing metadata database to update.
    :param end_block: Block used to build WTGXX metadata.
    :param row: WTGXX scan row.
    """

    chain_id = WTGXX_ETHEREUM.chain_id
    previous_watermark = vault_db.last_scanned_block.get(chain_id)
    vault_db.update_leads_and_rows(
        chain_id,
        end_block,
        {WTGXX_ETHEREUM.token: create_lead(WTGXX_ETHEREUM.first_seen_at_block, WTGXX_ETHEREUM.first_seen_at)},
        {VaultSpec(chain_id, WTGXX_ETHEREUM.token): row},
    )
    if previous_watermark is None:
        del vault_db.last_scanned_block[chain_id]
    else:
        vault_db.last_scanned_block[chain_id] = previous_watermark


def resolve_start_block() -> int:
    """Choose the earliest WTGXX history start block.

    HyperSync fills any missing timestamp cache entries, so the scan starts at
    deployment unless the operator explicitly supplies ``START_BLOCK``.

    :return: Explicit override or the WTGXX deployment block.
    """

    return int(os.environ["START_BLOCK"]) if os.environ.get("START_BLOCK") else WTGXX_ETHEREUM.first_seen_at_block


def run_backfill(
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
    """Run the complete address-scoped WTGXX migration.

    :param dry_run: Do not write metadata, states or Parquet rows.
    :param scan_prices: Rebuild WTGXX raw history.
    :param clean_prices: Replace only WTGXX cleaned history after raw scan.
    :param frequency: Shared scanner sample frequency.
    :param vault_db_path: Metadata database path.
    :param raw_price_path: Raw price Parquet path.
    :param cleaned_price_path: Cleaned price Parquet path.
    :param reader_state_path: Reader-state pickle path.
    """

    if not dry_run:
        require_price_scan_key()
    rpc_url = read_json_rpc_url(WTGXX_ETHEREUM.chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    end_block = web3.eth.block_number
    vault_db = read_vault_database(vault_db_path)
    token_cache = TokenDiskCache()
    detection = create_detection(WTGXX_ETHEREUM.first_seen_at_block, WTGXX_ETHEREUM.first_seen_at)
    row = create_vault_scan_record(web3, detection=detection, block_identifier=end_block, token_cache=token_cache)
    if not dry_run:
        upsert_selected_metadata(vault_db, end_block=end_block, row=row)
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.write(vault_db_path)
    if not scan_prices or dry_run:
        return
    vault = create_vault_instance(web3, WTGXX_ETHEREUM.token, features={ERC4626Feature.wisdomtree_like}, token_cache=token_cache)
    if vault is None:
        message = "Could not create WTGXX adapter"
        raise RuntimeError(message)
    vault.first_seen_at_block = WTGXX_ETHEREUM.first_seen_at_block
    states = remove_selected_reader_states(read_reader_states(reader_state_path))
    hypersync = configure_hypersync_from_env(web3).hypersync_client
    if hypersync is None:
        message = "WTGXX price history requires a configured HyperSync client"
        raise RuntimeError(message)
    result = scan_historical_prices_to_parquet(
        output_fname=raw_price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(rpc_url, retries=5),
        vaults=[vault],
        start_block=resolve_start_block(),
        end_block=end_block,
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        chunk_size=32,
        token_cache=token_cache,
        frequency=frequency,
        reader_states=states,
        hypersync_client=hypersync,
        vault_addresses=selected_vault_addresses(),
    )
    if not dry_run:
        write_reader_states(reader_state_path, result["reader_states"])
        if clean_prices:
            replace_cleaned_vault_histories(selected_vault_spec_ids(), vault_db_path=vault_db_path, raw_price_df_path=raw_price_path, cleaned_price_df_path=cleaned_price_path, logger=logger.info)
        token_cache.commit()


def main() -> None:
    """Read configuration and execute the scoped migration."""

    run_backfill(
        dry_run=parse_bool_env("DRY_RUN"),
        scan_prices=parse_bool_env("WISDOMTREE_SCAN_PRICES", default=True),
        clean_prices=parse_bool_env("WISDOMTREE_CLEAN_PRICES", default=True),
        frequency=os.environ.get("FREQUENCY", "1d"),
        vault_db_path=parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE),
        raw_price_path=parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE),
        cleaned_price_path=parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE),
        reader_state_path=parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE),
    )


if __name__ == "__main__":
    main()
