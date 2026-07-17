#!/usr/bin/env python3
"""Register reviewed Libeara ULTRA metadata without inventing price history.

ULTRA has no verified public NAV/share source. This address-scoped migration
therefore upserts only its hardcoded lead and current scan metadata. It does
not alter the shared Arbitrum discovery cursor, reader state, or raw and
cleaned price Parquet files. ``DRY_RUN`` defaults to ``true``.
"""

import logging
import os
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.libeara.constants import LIBEARA_ULTRA_ARBITRUM
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read a conventional boolean environment variable.

    :param name: Environment-variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def create_detection() -> ERC4262VaultDetection:
    """Create the reviewed ULTRA scanner detection.

    :return: Hardcoded, event-independent detection data.
    """

    product = LIBEARA_ULTRA_ARBITRUM
    return ERC4262VaultDetection(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at, features={ERC4626Feature.libeara_like}, updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)


def create_lead() -> PotentialVaultMatch:
    """Create the reviewed ULTRA discovery lead.

    :return: Hardcoded, event-independent lead data.
    """

    product = LIBEARA_ULTRA_ARBITRUM
    return PotentialVaultMatch(chain=product.chain_id, address=product.token, first_seen_at_block=product.first_seen_at_block, first_seen_at=product.first_seen_at, deposit_count=0, withdrawal_count=0)


def upsert_ultra_metadata(vault_db: VaultDatabase, row: dict) -> None:
    """Upsert ULTRA while preserving all shared scanner watermarks.

    This function deliberately avoids
    :meth:`VaultDatabase.update_leads_and_rows`, because a protocol-specific
    repair must not advance the chain-wide Arbitrum discovery cursor.

    :param vault_db: Existing shared vault metadata database.
    :param row: Fresh ULTRA metadata row.
    :return: ``None``.
    """

    product = LIBEARA_ULTRA_ARBITRUM
    spec = VaultSpec(product.chain_id, product.token)
    vault_db.leads[spec] = create_lead()
    vault_db._merge_rows({spec: row})


def main() -> None:
    """Run the ULTRA metadata-only migration.

    :return: ``None``.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    product = LIBEARA_ULTRA_ARBITRUM
    dry_run = parse_bool_env("DRY_RUN", default=True)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    logger.info(
        "Libeara ULTRA migration plan: chain=%s address=%s first_block=%s dry_run=%s; reader state and price Parquet files remain untouched",
        product.chain_id,
        product.token,
        product.first_seen_at_block,
        dry_run,
    )
    if dry_run:
        return

    web3 = create_multi_provider_web3(read_json_rpc_url(product.chain_id))
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    if end_block < product.first_seen_at_block:
        raise ValueError(f"END_BLOCK must be at least {product.first_seen_at_block}")
    row = create_vault_scan_record(web3, detection=create_detection(), block_identifier=end_block, token_cache=TokenDiskCache())
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    upsert_ultra_metadata(vault_db, row)
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
    logger.info("Added ULTRA lead and metadata to %s", vault_db_path)


if __name__ == "__main__":
    main()
