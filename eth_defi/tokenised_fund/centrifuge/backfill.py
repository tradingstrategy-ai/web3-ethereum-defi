"""Register the reviewed JTRSY Tranche token without rewriting price history.

The direct JTRSY ``Tranche`` token has no authoritative NAV/share-price
accessor. This migration therefore only upserts its lead and scan metadata;
it intentionally does not alter the shared raw/cleaned price Parquet files or
reader-state pickle. Existing unrelated vault records remain untouched.

Run with::

    source .local-test.env && PROTOCOLS=centrifuge poetry run python scripts/backfill-tokenised-funds.py

Set ``DRY_RUN=true`` to inspect the planned write. Set ``VAULT_DB_PATH`` to
use an alternative metadata database, and ``END_BLOCK`` to choose the metadata
block instead of the current Ethereum head.
"""

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
from eth_defi.tokenised_fund.centrifuge.constants import JTRSY_ETHEREUM
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment setting.

    :param name:
        Environment variable name.
    :param default:
        Value to return if the variable is absent.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_vault_database(path: Path) -> VaultDatabase:
    """Load an existing vault database or initialise an empty one.

    :param path:
        Vault metadata pickle location.
    :return:
        Existing or empty database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def upsert_jtrsy_metadata_preserving_discovery_cursor(
    vault_db: VaultDatabase,
    lead: PotentialVaultMatch,
    row: VaultRow,
) -> None:
    """Upsert JTRSY metadata without changing chain-wide discovery state.

    A targeted migration must not advance or initialise the Ethereum discovery
    cursor because unrelated contracts may still need to be discovered below
    the metadata block used for this repair.

    :param vault_db:
        Existing vault metadata database.
    :param lead:
        Reviewed JTRSY hardcoded lead.
    :param row:
        Fresh JTRSY scan row.
    :return:
        None.
    """

    spec = VaultSpec(JTRSY_ETHEREUM.chain_id, JTRSY_ETHEREUM.token)
    vault_db.leads[spec] = lead
    vault_db._merge_rows({spec: row})


def main() -> None:
    """Upsert the JTRSY lead and metadata row only.

    This deliberately avoids a historical price scan. A price rewrite with no
    authoritative NAV would add null-valued history and risk replacing useful
    unrelated Parquet state.
    """

    product = JTRSY_ETHEREUM
    dry_run = parse_bool_env("DRY_RUN")
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    web3 = create_multi_provider_web3(read_json_rpc_url(product.chain_id))
    metadata_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    detection = ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.centrifuge_tranche_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )
    lead = PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )

    if dry_run:
        logger.info("Would add JTRSY lead and metadata to %s at Ethereum block %d", vault_db_path, metadata_block)
        return

    vault_db = read_vault_database(vault_db_path)
    row = create_vault_scan_record(
        web3,
        detection=detection,
        block_identifier=metadata_block,
        token_cache=TokenDiskCache(),
    )
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_jtrsy_metadata_preserving_discovery_cursor(vault_db, lead, row)
    vault_db.write(vault_db_path)
    logger.info("Added JTRSY lead and metadata to %s; price and reader-state files were not changed", vault_db_path)


if __name__ == "__main__":
    setup_console_logging()
    main()
