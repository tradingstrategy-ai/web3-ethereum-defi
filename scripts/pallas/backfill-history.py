#!/usr/bin/env python3
"""Upsert reviewed Pallas vault metadata without disturbing historical data.

Pallas uses ERC-7540 request-and-claim vaults on HyperEVM.  Their queue flow
does not provide a reviewed, deterministic historical share-price source, so
this migration deliberately registers only the two reviewed addresses and
refreshes their live metadata.  Normal lead discovery receives the same
hardcoded leads through :mod:`eth_defi.erc_4626.discovery_base`.

The migration is address-scoped.  It preserves all unrelated metadata rows,
the HyperEVM discovery cursor, reader state and raw/cleaned price Parquet
files.  It starts in dry-run mode; inspect the plan and then set
``DRY_RUN=false`` to perform the metadata write.

.. code-block:: shell

    source .local-test.env && poetry run python scripts/pallas/backfill-history.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/pallas/backfill-history.py

Configuration is through environment variables:

``DRY_RUN``
    Print the selected Pallas addresses without writing. Defaults to ``true``.

``VAULT_DB_PATH``
    Optional metadata database path. Defaults to the active pipeline metadata
    database.

``END_BLOCK``
    Block used for the live metadata snapshot. Defaults to the current
    HyperEVM block.

``BACKUP_PATH``
    Optional backup path. A timestamped sibling is used by default before a
    non-dry-run update of an existing metadata database.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.
"""

import datetime
import logging
import os
import shutil
from pathlib import Path

from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.pallas.constants import HYPERLIQUID_CHAIN_ID, PALLAS_HARDCODED_LEADS
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value to use when the variable is absent.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_vault_database_path() -> Path:
    """Resolve the metadata database path without selecting price-state files.

    :return:
        Explicit ``VAULT_DB_PATH`` or the active pipeline metadata database.
    """

    configured_path = os.environ.get("VAULT_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return get_pipeline_data_dir() / "vault-metadata-db.pickle"


def resolve_backup_path(vault_db_path: Path) -> Path:
    """Build a non-overwriting backup filename for an existing metadata database.

    :param vault_db_path:
        Metadata database selected for the update.
    :return:
        Explicit ``BACKUP_PATH`` or a timestamped sibling path.
    """

    configured_path = os.environ.get("BACKUP_PATH")
    if configured_path:
        return Path(configured_path).expanduser()

    timestamp = native_datetime_utc_now().strftime("%Y%m%d-%H%M%S")
    return vault_db_path.with_name(f"{vault_db_path.stem}.before-pallas-metadata-migration-{timestamp}{vault_db_path.suffix}")


def create_pallas_detection(
    chain_id: int,
    address: HexAddress,
    first_seen_at_block: int,
    first_seen_at: datetime.datetime,
) -> ERC4262VaultDetection:
    """Create a reviewed Pallas classification record from a hardcoded lead.

    :param chain_id:
        HyperEVM chain id from the reviewed lead.
    :param address:
        Reviewed Pallas vault address.
    :param first_seen_at_block:
        Proxy deployment block.
    :param first_seen_at:
        Proxy deployment timestamp stored as naive UTC.
    :return:
        Hardcoded Pallas feature detection for one vault.
    """

    return ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=first_seen_at_block,
        first_seen_at=first_seen_at,
        features={ERC4626Feature.pallas_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_pallas_lead(
    chain_id: int,
    address: HexAddress,
    first_seen_at_block: int,
    first_seen_at: datetime.datetime,
) -> PotentialVaultMatch:
    """Create a durable hardcoded Pallas discovery lead.

    :param chain_id:
        HyperEVM chain id from the reviewed lead.
    :param address:
        Reviewed Pallas vault address.
    :param first_seen_at_block:
        Proxy deployment block.
    :param first_seen_at:
        Proxy deployment timestamp stored as naive UTC.
    :return:
        Lead that remains available independently of historical event scans.
    """

    return PotentialVaultMatch(
        chain=chain_id,
        address=address,
        first_seen_at_block=first_seen_at_block,
        first_seen_at=first_seen_at,
    )


def upsert_pallas_metadata(vault_db: VaultDatabase, leads: dict[HexAddress, PotentialVaultMatch], rows: dict[VaultSpec, VaultRow]) -> None:
    """Upsert only reviewed Pallas records without moving discovery progress.

    ``VaultDatabase.update_leads_and_rows()`` is intentionally not used because
    it also advances HyperEVM's chain-wide discovery cursor.  Moving that
    cursor during an address-scoped repair could make later normal discovery
    miss unrelated vaults.

    :param vault_db:
        Existing vault metadata database loaded from disk.
    :param leads:
        Reviewed Pallas leads keyed by address.
    :param rows:
        Fresh Pallas metadata records keyed by their chain/address specification.
    :return:
        ``None`` after mutating only the reviewed Pallas entries in memory.
    """

    vault_db.leads.update({VaultSpec(lead.chain, address): lead for address, lead in leads.items()})
    vault_db._merge_rows(rows)


def main() -> None:
    """Run the address-scoped Pallas metadata migration.

    The script never opens reader-state or price-Parquet files.  It builds all
    fresh rows before backing up or writing the metadata pickle, so an RPC
    failure leaves existing persistent state unchanged.

    :return:
        ``None`` after displaying the plan and optionally writing metadata.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    vault_db_path = resolve_vault_database_path()
    plan = [
        {
            "chain": chain_id,
            "address": address,
            "first block": first_seen_at_block,
            "first seen": first_seen_at.isoformat(),
        }
        for chain_id, address, first_seen_at_block, first_seen_at in PALLAS_HARDCODED_LEADS
    ]
    logger.info("Pallas metadata migration plan; dry_run=%s\n%s", dry_run, tabulate(plan, headers="keys", tablefmt="github"))

    if dry_run:
        return

    web3: Web3 = create_multi_provider_web3(read_json_rpc_url(HYPERLIQUID_CHAIN_ID), retries=2, hint="Pallas metadata migration")
    if web3.eth.chain_id != HYPERLIQUID_CHAIN_ID:
        message = f"Pallas metadata migration requires HyperEVM chain {HYPERLIQUID_CHAIN_ID}, got {web3.eth.chain_id}"
        raise ValueError(message)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    vault_db_existed = vault_db_path.exists()
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_existed else VaultDatabase()
    token_cache = TokenDiskCache()
    leads = {address: create_pallas_lead(chain_id, address, first_seen_at_block, first_seen_at) for chain_id, address, first_seen_at_block, first_seen_at in PALLAS_HARDCODED_LEADS}
    rows = {detection.get_spec(): create_vault_scan_record(web3, detection, block_identifier=end_block, token_cache=token_cache) for detection in (create_pallas_detection(chain_id, address, first_seen_at_block, first_seen_at) for chain_id, address, first_seen_at_block, first_seen_at in PALLAS_HARDCODED_LEADS)}

    if vault_db_existed:
        backup_path = resolve_backup_path(vault_db_path)
        if backup_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing backup: {backup_path}")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vault_db_path, backup_path)
        logger.info("Backup written to %s", backup_path)

    upsert_pallas_metadata(vault_db, leads, rows)
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
    token_cache.commit()
    logger.info("Upserted %d Pallas metadata rows at block %d into %s", len(rows), end_block, vault_db_path)


if __name__ == "__main__":
    main()
