#!/usr/bin/env python3
"""Backfill Barker H1 metadata into the shared vault database.

This is a targeted migration for the reviewed Barker H1 deployment on
HyperEVM. It never restarts chain discovery and updates only the one reviewed
vault lead and metadata row. Price history is intentionally unsupported: H1's
epoch lifecycle and unverified implementation require a dedicated historical
reader review before historical rows can be safely rewritten.

Usage:

.. code-block:: shell

    source .local-test.env && \\
      BARKER_SCAN_PRICES=false \\
      poetry run python scripts/barker/backfill-history.py

Environment variables:

- ``DRY_RUN``: Set to ``true`` to display the plan without writing. Defaults
  to ``false``.
- ``VAULT_DB_PATH``: Optional vault metadata database path. Defaults to the
  shared production path.
- ``BARKER_SCAN_PRICES``: Must remain ``false``. It is present to make an
  unsafe historical rewrite fail explicitly rather than silently modifying
  existing price data.
"""

import datetime
import logging
import os
import sys
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.barker.vault import BARKER_H1_VAULT_ADDRESS
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


#: HyperEVM chain ID.
HYPEREVM_CHAIN_ID = 999

#: First deployment block of the reviewed Barker H1 vault.
BARKER_H1_DEPLOYMENT_BLOCK = 41_757_863

#: Naive UTC timestamp of :data:`BARKER_H1_DEPLOYMENT_BLOCK`.
BARKER_H1_DEPLOYMENT_TIME = datetime.datetime(2026, 7, 29, 12, 47)  # noqa: DTZ001 - repository timestamps are naive UTC.


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read one boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the environment variable is absent.

    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def create_barker_detection() -> ERC4262VaultDetection:
    """Build the synthetic detection record for the reviewed H1 vault.

    The migration has no reason to rediscover unrelated HyperEVM events: the
    deployment block and address have been reviewed directly.

    :return:
        Detection record compatible with the shared vault scanner.
    """

    return ERC4262VaultDetection(
        chain=HYPEREVM_CHAIN_ID,
        address=BARKER_H1_VAULT_ADDRESS,
        first_seen_at_block=BARKER_H1_DEPLOYMENT_BLOCK,
        first_seen_at=BARKER_H1_DEPLOYMENT_TIME,
        features={ERC4626Feature.barker_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def create_barker_lead() -> PotentialVaultMatch:
    """Build the scanner lead for the reviewed H1 vault.

    :return:
        Lead with the reviewed deployment block and timestamp.
    """

    return PotentialVaultMatch(
        chain=HYPEREVM_CHAIN_ID,
        address=BARKER_H1_VAULT_ADDRESS,
        first_seen_at_block=BARKER_H1_DEPLOYMENT_BLOCK,
        first_seen_at=BARKER_H1_DEPLOYMENT_TIME,
        deposit_count=0,
        withdrawal_count=0,
    )


def backfill_barker_metadata(vault_db_path: Path, *, dry_run: bool) -> dict[str, object]:
    """Upsert only the Barker H1 lead and metadata record.

    The operation scopes both the lead and row mappings to H1. The normal
    database method retains every other chain and vault row unchanged.

    :param vault_db_path:
        Existing shared vault metadata database, or a path for a new database.
    :param dry_run:
        If ``True``, perform RPC reads and report the planned row without a
        database write.

    :return:
        A tabular migration summary.
    """

    web3 = create_multi_provider_web3(read_json_rpc_url(HYPEREVM_CHAIN_ID))
    detection = create_barker_detection()
    token_cache = TokenDiskCache()
    metadata_block = web3.eth.block_number
    row = create_vault_scan_record(
        web3,
        detection=detection,
        block_identifier=metadata_block,
        token_cache=token_cache,
    )
    spec = VaultSpec(HYPEREVM_CHAIN_ID, BARKER_H1_VAULT_ADDRESS)

    if not dry_run:
        vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db.update_leads_and_rows(
            chain_id=HYPEREVM_CHAIN_ID,
            last_scanned_block=metadata_block,
            leads={BARKER_H1_VAULT_ADDRESS: create_barker_lead()},
            rows={spec: row},
        )
        vault_db.write(vault_db_path)
        token_cache.commit()
        logger.info("Updated Barker H1 metadata in %s", vault_db_path)

    return {
        "chain": get_chain_name(HYPEREVM_CHAIN_ID),
        "chain_id": HYPEREVM_CHAIN_ID,
        "rpc": get_json_rpc_env(HYPEREVM_CHAIN_ID),
        "vault": BARKER_H1_VAULT_ADDRESS,
        "metadata_block": metadata_block,
        "write": "dry-run" if dry_run else "updated",
    }


def main() -> None:
    """Run the address-scoped Barker metadata migration.

    :raise ValueError:
        If a price-history rewrite is requested before a dedicated historical
        reader has been reviewed.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/barker-backfill-history.log"))
    if parse_bool_env("BARKER_SCAN_PRICES", default=False):
        message = "BARKER_SCAN_PRICES is unsupported; the migration must not rewrite Barker price history"
        raise ValueError(message)

    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)).expanduser()
    dry_run = parse_bool_env("DRY_RUN", default=False)
    summary = backfill_barker_metadata(vault_db_path, dry_run=dry_run)
    print(tabulate([summary], headers="keys", tablefmt="github"))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        logger.exception("Barker backfill failed: %s", error)
        sys.exit(1)
