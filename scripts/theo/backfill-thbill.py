#!/usr/bin/env python3
"""Register canonical Ethereum thBILL without rewriting price history.

thBILL has no reviewed scalar NAV/share source: Theo's iToken accounting uses
a basket of assets. This address-scoped migration therefore upserts only the
thBILL discovery lead and scan metadata. It intentionally leaves raw and
cleaned price Parquet files plus reader-state data untouched, and restores the
existing Ethereum discovery watermark after the metadata write.

Run with ``source .local-test.env && poetry run python scripts/theo/backfill-thbill.py``.
Set ``DRY_RUN=true`` to inspect the plan. ``VAULT_DB_PATH`` and ``END_BLOCK``
are optional overrides.
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
from eth_defi.tokenised_fund.theo.constants import THBILL_ETHEREUM
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment value.

    :param name: Environment variable to read.
    :param default: Value used when the variable is not set.
    :return: Parsed boolean.
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
    """Upsert thBILL metadata while preserving all unrelated state."""

    product = THBILL_ETHEREUM
    dry_run = parse_bool_env("DRY_RUN")
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    web3 = create_multi_provider_web3(read_json_rpc_url(product.chain_id))
    metadata_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    detection = ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.theo_itoken_like},
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
        logger.info("Would add thBILL lead and metadata to %s at Ethereum block %d", vault_db_path, metadata_block)
        return

    vault_db = read_vault_database(vault_db_path)
    prior_watermark = vault_db.last_scanned_block.get(product.chain_id)
    row = create_vault_scan_record(web3, detection=detection, block_identifier=metadata_block, token_cache=TokenDiskCache())
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.update_leads_and_rows(
        chain_id=product.chain_id,
        last_scanned_block=metadata_block,
        leads={product.token: lead},
        rows={VaultSpec(product.chain_id, product.token): row},
    )
    # This migration touches one address only: do not advance the global chain
    # discovery cursor, which would skip unrelated leads on a later scan.
    if prior_watermark is None:
        vault_db.last_scanned_block.pop(product.chain_id, None)
    else:
        vault_db.last_scanned_block[product.chain_id] = prior_watermark
    vault_db.write(vault_db_path)
    logger.info("Added thBILL lead and metadata to %s; reader state and price Parquet files were not changed", vault_db_path)


if __name__ == "__main__":
    setup_console_logging()
    main()
