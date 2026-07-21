"""Reclassify the reviewed Bulla Factoring pool without rewriting price history.

The reviewed Arbitrum Bulla pool was already discovered from ERC-4626 events,
but historical metadata records predate the Bulla Network classifier. This
address-scoped migration upserts only its discovery lead and scan row. It does
not alter raw or cleaned price Parquet files, reader-state data, or any other
vault's metadata.

Run with ``source .local-test.env && poetry run python scripts/bulla/backfill-history.py``.
Set ``DRY_RUN=true`` to inspect the plan. ``VAULT_DB_PATH`` and ``END_BLOCK``
are optional overrides.
"""

import datetime
import logging
import os
from pathlib import Path

from eth_defi.coloured_logging import setup_console_logging
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

BULLA_CHAIN_ID = 42161
BULLA_VAULT_ADDRESS = "0xc099773267308d8e9e805f47eabf9ab13bbc9e37"
BULLA_CREATED_AT_BLOCK = 455_955_959
BULLA_CREATED_AT = datetime.datetime(2026, 4, 24, 19, 8, 48)  # noqa: DTZ001 - repository timestamps are naive UTC


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment value.

    :param name: Environment variable to read.
    :param default: Value used when the variable is unset.
    :return: Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_vault_database(path: Path) -> VaultDatabase:
    """Read existing metadata or create an empty database.

    :param path: Metadata database path.
    :return: Existing or new vault database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def main() -> None:
    """Upsert Bulla metadata while preserving unrelated scanner state."""

    dry_run = parse_bool_env("DRY_RUN")
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    web3 = create_multi_provider_web3(read_json_rpc_url(BULLA_CHAIN_ID))
    metadata_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    detection = ERC4262VaultDetection(
        chain=BULLA_CHAIN_ID,
        address=BULLA_VAULT_ADDRESS,
        first_seen_at_block=BULLA_CREATED_AT_BLOCK,
        first_seen_at=BULLA_CREATED_AT,
        features={ERC4626Feature.bulla_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )
    lead = PotentialVaultMatch(
        chain=BULLA_CHAIN_ID,
        address=BULLA_VAULT_ADDRESS,
        first_seen_at_block=BULLA_CREATED_AT_BLOCK,
        first_seen_at=BULLA_CREATED_AT,
        deposit_count=0,
        withdrawal_count=0,
    )
    if dry_run:
        logger.info("Would add Bulla Network metadata to %s at Arbitrum block %d", vault_db_path, metadata_block)
        return

    vault_db = read_vault_database(vault_db_path)
    prior_watermark = vault_db.last_scanned_block.get(BULLA_CHAIN_ID)
    row = create_vault_scan_record(web3, detection=detection, block_identifier=metadata_block, token_cache=TokenDiskCache())
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.update_leads_and_rows(
        chain_id=BULLA_CHAIN_ID,
        last_scanned_block=metadata_block,
        leads={BULLA_VAULT_ADDRESS: lead},
        rows={VaultSpec(BULLA_CHAIN_ID, BULLA_VAULT_ADDRESS): row},
    )
    # Do not advance the all-vault discovery cursor when changing one known
    # address: doing so could skip unrelated leads on the next incremental scan.
    if prior_watermark is None:
        vault_db.last_scanned_block.pop(BULLA_CHAIN_ID, None)
    else:
        vault_db.last_scanned_block[BULLA_CHAIN_ID] = prior_watermark
    vault_db.write(vault_db_path)
    logger.info("Added Bulla Network metadata to %s; price and reader-state data were not changed", vault_db_path)


if __name__ == "__main__":
    setup_console_logging()
    main()
