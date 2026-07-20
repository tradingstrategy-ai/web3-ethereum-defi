"""Add reviewed Sygnum FILQ share-class leads without touching unrelated histories.

FILQ currently has no public, verified NAV-history route. This generated
migration therefore writes only hardcoded metadata leads. It explicitly
does not reset reader state or alter raw/cleaned Parquet files: creating price
rows without NAV would be misleading, and retaining those files prevents an
accidental full-chain rescan.

Run with ``source .local-test.env && PROTOCOLS=sygnum poetry run python scripts/backfill-tokenised-funds.py``.
Set ``DRY_RUN=true`` to inspect the address-scoped plan without writing.
``VAULT_DB_PATH`` and ``END_BLOCK`` may be overridden for controlled runs.
"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, SYGNUM_ETHEREUM_CHAIN_ID
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read a conventional Boolean environment variable.

    :param name: Environment-variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed Boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def create_detection(address: str = FILQ_A_ETHEREUM_ADDRESS, first_seen_at_block: int = FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, first_seen_at=FILQ_A_ETHEREUM_FIRST_SEEN_AT) -> ERC4262VaultDetection:
    """Create a reviewed FILQ share-class detection.

    :return: Hardcoded non-event-derived FILQ detection.
    """

    return ERC4262VaultDetection(chain=SYGNUM_ETHEREUM_CHAIN_ID, address=address, first_seen_at_block=first_seen_at_block, first_seen_at=first_seen_at, features={ERC4626Feature.sygnum_like}, updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)


def create_lead(address: str = FILQ_A_ETHEREUM_ADDRESS, first_seen_at_block: int = FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, first_seen_at=FILQ_A_ETHEREUM_FIRST_SEEN_AT) -> PotentialVaultMatch:
    """Create an address-scoped discovery lead.

    :return: FILQ potential vault match.
    """

    return PotentialVaultMatch(chain=SYGNUM_ETHEREUM_CHAIN_ID, address=address, first_seen_at_block=first_seen_at_block, first_seen_at=first_seen_at, deposit_count=0, withdrawal_count=0)


def upsert_filq_metadata(vault_db: VaultDatabase, rows: dict, end_block: int) -> None:
    """Upsert FILQ share classes while restoring the Ethereum discovery cursor.

    :param vault_db: Existing shared vault database.
    :param rows: Fresh FILQ metadata rows.
    :param end_block: Block at which metadata was read.
    :return: ``None``.
    """

    had_cursor = SYGNUM_ETHEREUM_CHAIN_ID in vault_db.last_scanned_block
    previous_cursor = vault_db.last_scanned_block.get(SYGNUM_ETHEREUM_CHAIN_ID)
    products = (
        (FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
        (FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
    )
    vault_db.update_leads_and_rows(
        chain_id=SYGNUM_ETHEREUM_CHAIN_ID,
        last_scanned_block=end_block,
        leads={address: create_lead(address, first_seen_at_block, first_seen_at) for address, first_seen_at_block, first_seen_at in products},
        rows=rows,
    )
    if had_cursor:
        assert previous_cursor is not None
        vault_db.last_scanned_block[SYGNUM_ETHEREUM_CHAIN_ID] = previous_cursor
    else:
        vault_db.last_scanned_block.pop(SYGNUM_ETHEREUM_CHAIN_ID, None)


def main() -> None:
    """Run the FILQ metadata-only migration.

    :return: ``None``.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=False)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    web3 = create_multi_provider_web3(read_json_rpc_url(SYGNUM_ETHEREUM_CHAIN_ID))
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    if end_block < FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK:
        raise ValueError(f"END_BLOCK must be at least {FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK}")
    plan = tabulate(
        [
            {"chain": "Ethereum", "address": FILQ_A_ETHEREUM_ADDRESS, "end_block": end_block, "write_prices": False, "dry_run": dry_run},
            {"chain": "Ethereum", "address": FILQ_D_ETHEREUM_ADDRESS, "end_block": end_block, "write_prices": False, "dry_run": dry_run},
        ],
        headers="keys",
        tablefmt="github",
    )
    logger.info("Sygnum FILQ migration plan:\n%s", plan)
    if dry_run:
        logger.info("DRY RUN: would upsert FILQ metadata only; reader state and Parquet files remain untouched")
        return
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    rows = {
        VaultSpec(SYGNUM_ETHEREUM_CHAIN_ID, address): create_vault_scan_record(web3, detection=create_detection(address, first_seen_at_block, first_seen_at), block_identifier=end_block, token_cache=TokenDiskCache())
        for address, first_seen_at_block, first_seen_at in (
            (FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
            (FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
        )
    }
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_filq_metadata(vault_db, rows, end_block)
    vault_db.write(vault_db_path)


if __name__ == "__main__":
    main()
