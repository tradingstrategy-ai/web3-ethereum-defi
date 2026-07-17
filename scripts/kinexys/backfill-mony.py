#!/usr/bin/env python3
"""Register the MONY FACT Diamond in the shared vault metadata database.

MONY has no public, verified NAV or share-price source. This migration therefore
only writes its exact Ethereum discovery lead and current metadata row. It
intentionally does not rewrite the raw or cleaned price Parquet files, and it
does not remove or modify any historical reader state: supply-only rows without
a price are not a usable fund-price history.

Usage:

.. code-block:: shell

    source .local-test.env
    export JSON_RPC_ETHEREUM="https://your-archive-ethereum-rpc"
    poetry run python scripts/kinexys/backfill-mony.py

Set ``DRY_RUN=true`` to validate the planned MONY row without writing. Optional
``END_BLOCK`` selects the metadata snapshot block and ``VAULT_DB_PATH`` selects
the metadata database. No raw-price, cleaned-price, or reader-state path is
accepted by design, preventing this supply-only migration from altering those
datasets.
"""

import logging
import os
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import (
    ODA_FACT_MONY_ADDRESS,
    ODA_FACT_MONY_FIRST_SEEN_AT,
    ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK,
)
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is unset.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_vault_database_path() -> Path:
    """Get the selected MONY metadata database path.

    :return:
        Configured metadata database path, or the shared default.
    """

    return Path(os.environ["VAULT_DB_PATH"]).expanduser() if os.environ.get("VAULT_DB_PATH") else DEFAULT_VAULT_DATABASE


def create_mony_detection() -> ERC4262VaultDetection:
    """Create the exact hardcoded MONY detection record.

    :return:
        Discovery-compatible MONY FACT detection.
    """

    return ERC4262VaultDetection(
        chain=1,
        address=ODA_FACT_MONY_ADDRESS,
        first_seen_at_block=ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK,
        first_seen_at=ODA_FACT_MONY_FIRST_SEEN_AT,
        features={ERC4626Feature.oda_fact_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_mony_lead() -> PotentialVaultMatch:
    """Create the exact hardcoded MONY discovery lead.

    :return:
        Zero-event lead for the FACT Diamond deployment.
    """

    return PotentialVaultMatch(
        chain=1,
        address=ODA_FACT_MONY_ADDRESS,
        first_seen_at_block=ODA_FACT_MONY_FIRST_SEEN_AT_BLOCK,
        first_seen_at=ODA_FACT_MONY_FIRST_SEEN_AT,
        deposit_count=0,
        withdrawal_count=0,
    )


def read_vault_database(path: Path) -> VaultDatabase:
    """Load an existing metadata database or create an empty one.

    :param path:
        Metadata database input path.
    :return:
        Existing database, or an empty database when it has not been created.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def update_mony_metadata(
    database: VaultDatabase,
    row: dict,
    *,
    dry_run: bool,
    output_path: Path,
) -> VaultSpec:
    """Insert only MONY metadata without disturbing pipeline cursor or history.

    The shared ``update_leads_and_rows`` helper advances the Ethereum discovery
    cursor. A one-vault repair must not do that, because it has not scanned
    other Ethereum contracts. Directly updating this exact specification keeps
    every unrelated row, lead, reader-state entry and Parquet row intact.

    :param database:
        Loaded shared vault metadata database.
    :param row:
        Fresh MONY metadata scan row.
    :param dry_run:
        Whether output writes are disabled.
    :param output_path:
        Metadata database destination.
    :return:
        MONY vault specification inserted into the metadata database.
    """

    spec = VaultSpec(1, ODA_FACT_MONY_ADDRESS)
    if dry_run:
        return spec

    database.leads[spec] = create_mony_lead()
    database.rows[spec] = row
    output_path.parent.mkdir(parents=True, exist_ok=True)
    database.write(output_path)
    return spec


def main() -> None:
    """Create or refresh the MONY discovery lead and metadata row."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/kinexys-mony-backfill.log"))
    dry_run = parse_bool_env("DRY_RUN")
    web3 = create_multi_provider_web3(read_json_rpc_url(1))
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else web3.eth.block_number
    database_path = get_vault_database_path()
    token_cache = TokenDiskCache()
    row = create_vault_scan_record(
        web3,
        detection=create_mony_detection(),
        block_identifier=end_block,
        token_cache=token_cache,
    )
    spec = update_mony_metadata(read_vault_database(database_path), row, dry_run=dry_run, output_path=database_path)
    if not dry_run:
        token_cache.commit()

    logger.info(
        "MONY metadata %s: vault=%s block=%d database=%s; raw and cleaned price Parquet plus reader state were intentionally left unchanged because MONY has no verified public NAV history",
        "validated" if dry_run else "written",
        spec.as_string_id(),
        end_block,
        database_path,
    )


if __name__ == "__main__":
    main()
