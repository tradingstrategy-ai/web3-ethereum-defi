"""Bootstrap Symbiotic Core V2 vault metadata without resetting whole-chain discovery.

Symbiotic's public application API lists current Core V2 vaults, whereas the
incremental vault scanner does not revisit already discovered generic ERC-4626
rows after a new protocol detector is added. This script uses the API list,
finds each targeted vault's proxy deployment block with archive RPC reads, and
rebuilds metadata only for those vaults.

The migration deliberately leaves reader-state pickles and Parquet price data
untouched. A later ordinary price scan can use the corrected metadata; no
unrelated chain state is restarted or rewritten.

Usage:

.. code-block:: shell

    source .local-test.env
    DRY_RUN=true poetry run python scripts/erc-4626/fix-symbiotic-vaults.py
    DRY_RUN=false poetry run python scripts/erc-4626/fix-symbiotic-vaults.py

Environment variables:

- ``DRY_RUN``: Report changes without writing the metadata database. Defaults
  to ``true``.
- ``JSON_RPC_ETHEREUM``: Archive-capable Ethereum JSON-RPC URL. Required.
- ``VAULT_DB_PATH``: Metadata database pickle. Defaults to the production path.
"""

import datetime
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests
from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata import fetch_symbiotic_offchain_vaults
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Symbiotic Core V2 currently operates on Ethereum mainnet only.
SYMBIOTIC_CHAIN_ID = 1


@dataclass(slots=True, frozen=True)
class SymbioticMigrationReference:
    """A reviewed Symbiotic Core V2 vault prepared for metadata migration."""

    #: Core V2 vault proxy address.
    address: HexAddress

    #: First Ethereum block with proxy bytecode.
    first_seen_at_block: int

    #: Naive UTC timestamp of :attr:`first_seen_at_block`.
    first_seen_at: datetime.datetime

    def get_spec(self) -> VaultSpec:
        """Return the production vault database identity for this vault.

        :return:
            Ethereum vault specification.
        """
        return VaultSpec(chain_id=SYMBIOTIC_CHAIN_ID, vault_address=self.address)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    """Read a conservative boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is not set.
    :return:
        Parsed boolean setting.
    :raises ValueError:
        If a set value is not a recognised boolean.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _has_code_at_block(web3: Web3, address: HexAddress, block_number: int) -> bool:
    """Check whether a proxy address has runtime bytecode at a block.

    :param web3:
        Archive-capable Ethereum client.
    :param address:
        Contract address to inspect.
    :param block_number:
        Historical block number to query.
    :return:
        ``True`` when the address has non-empty runtime bytecode.
    """
    return len(web3.eth.get_code(address, block_identifier=block_number)) > 0


def find_first_code_block(web3: Web3, address: HexAddress, latest_block: int) -> int:
    """Find the first block containing runtime bytecode for a target proxy.

    The binary search is bounded by the operator's latest Ethereum block and
    uses only targeted archive ``eth_getCode`` reads.

    :param web3:
        Archive-capable Ethereum client.
    :param address:
        Target vault proxy address.
    :param latest_block:
        Inclusive latest block upper bound.
    :return:
        First block with non-empty runtime bytecode.
    :raises ValueError:
        If the target has no bytecode at ``latest_block``.
    """
    if not _has_code_at_block(web3, address, latest_block):
        raise ValueError(f"No bytecode for {address} at latest block {latest_block:,}")

    low = 1
    high = latest_block
    while low < high:
        middle = (low + high) // 2
        if _has_code_at_block(web3, address, middle):
            high = middle
        else:
            low = middle + 1
    return low


def fetch_symbiotic_references(web3: Web3) -> list[SymbioticMigrationReference]:
    """Fetch current Core V2 API records and resolve their deployment blocks.

    :param web3:
        Archive-capable Ethereum client.
    :return:
        Current public Symbiotic V2 vaults with exact proxy deployment data.
    """
    latest_block = web3.eth.block_number
    references: list[SymbioticMigrationReference] = []
    for vault in fetch_symbiotic_offchain_vaults(vault_type="v2"):
        address = Web3.to_checksum_address(vault["address"])
        first_seen_at_block = find_first_code_block(web3, address, latest_block)
        timestamp = web3.eth.get_block(first_seen_at_block)["timestamp"]
        references.append(
            SymbioticMigrationReference(
                address=address,
                first_seen_at_block=first_seen_at_block,
                first_seen_at=native_datetime_utc_fromtimestamp(timestamp),
            )
        )
    return references


def create_detection(reference: SymbioticMigrationReference, features: set[ERC4626Feature], now_: datetime.datetime) -> ERC4262VaultDetection:
    """Create a persisted detector result for one reviewed Symbiotic vault.

    :param reference:
        Reviewed public API vault and deployment data.
    :param features:
        Features returned by the current targeted detector.
    :param now_:
        Naive UTC migration time.
    :return:
        Detection record compatible with the vault metadata database.
    :raises ValueError:
        If the target no longer matches the Symbiotic detector.
    """
    if ERC4626Feature.symbiotic_like not in features:
        raise ValueError(f"Target {reference.address} did not match the Symbiotic detector: {features}")
    return ERC4262VaultDetection(
        chain=SYMBIOTIC_CHAIN_ID,
        address=reference.address,
        first_seen_at_block=reference.first_seen_at_block,
        first_seen_at=reference.first_seen_at,
        features=features,
        updated_at=now_,
        deposit_count=5,
        redeem_count=0,
    )


def create_lead(reference: SymbioticMigrationReference, existing: PotentialVaultMatch | None) -> PotentialVaultMatch:
    """Create a targeted lead while preserving existing observed flow counts.

    :param reference:
        Reviewed current Symbiotic vault reference.
    :param existing:
        Existing lead, if the generic scanner had already discovered it.
    :return:
        Lead for the exact Symbiotic vault only.
    """
    return PotentialVaultMatch(
        chain=SYMBIOTIC_CHAIN_ID,
        address=reference.address,
        first_seen_at_block=reference.first_seen_at_block,
        first_seen_at=getattr(existing, "first_seen_at", reference.first_seen_at),
        deposit_count=max(getattr(existing, "deposit_count", 0), 5),
        withdrawal_count=getattr(existing, "withdrawal_count", 0),
    )


def create_backup_path(vault_db_path: Path, now_: datetime.datetime) -> Path:
    """Create a unique metadata database backup path.

    :param vault_db_path:
        Existing production metadata database path.
    :param now_:
        Naive UTC migration time.
    :return:
        Sibling non-overwriting backup path.
    """
    return vault_db_path.with_name(f"{vault_db_path.stem}.before-symbiotic-migration-{now_:%Y%m%dT%H%M%SZ}{vault_db_path.suffix}")


def run_migration(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, references: list[SymbioticMigrationReference], now_: datetime.datetime) -> list[dict[str, object]]:
    """Rebuild metadata and leads only for reviewed Symbiotic Core V2 vaults.

    :param web3:
        Ethereum client used for targeted classification and metadata reads.
    :param vault_db:
        In-memory production metadata database to update.
    :param token_cache:
        Shared token metadata cache.
    :param references:
        Public API vaults with verified deployment blocks.
    :param now_:
        Naive UTC migration time.
    :return:
        Tabular migration report rows.
    """
    report: list[dict[str, object]] = []
    latest_block = web3.eth.block_number
    for reference in references:
        spec = reference.get_spec()
        features = detect_vault_features(web3, reference.address, verbose=False)
        detection = create_detection(reference, features, now_)
        existing_row = vault_db.rows.get(spec)
        vault_db.leads[spec] = create_lead(reference, vault_db.leads.get(spec))
        vault_db.rows[spec] = create_vault_scan_record(web3, detection, latest_block, token_cache)
        report.append(
            {
                "address": reference.address,
                "first block": reference.first_seen_at_block,
                "previous protocol": existing_row.get("Protocol") if existing_row else "<new>",
                "new protocol": vault_db.rows[spec]["Protocol"],
            }
        )
    return report


def main() -> None:
    """Run the targeted Symbiotic metadata migration from environment settings.

    :return:
        ``None`` after reporting the planned or applied target-only migration.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    json_rpc_url = os.environ.get("JSON_RPC_ETHEREUM")
    if not json_rpc_url:
        message = "JSON_RPC_ETHEREUM is required for the Symbiotic migration"
        raise ValueError(message)

    dry_run = _parse_bool_env("DRY_RUN", default=True)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    now_ = native_datetime_utc_now()
    web3 = create_multi_provider_web3(json_rpc_url)
    if web3.eth.chain_id != SYMBIOTIC_CHAIN_ID:
        raise ValueError(f"JSON_RPC_ETHEREUM returned chain {web3.eth.chain_id}, expected {SYMBIOTIC_CHAIN_ID}")

    vault_db = VaultDatabase.read(vault_db_path)
    references = fetch_symbiotic_references(web3)
    logger.info("Prepared %d Symbiotic Core V2 migration targets", len(references))
    with tempfile.TemporaryDirectory(prefix="symbiotic-token-cache-") as cache_directory:
        report = run_migration(web3, vault_db, TokenDiskCache(Path(cache_directory) / "tokens.sqlite"), references, now_)
    print(tabulate(report, headers="keys", tablefmt="rounded_outline"))

    if dry_run:
        print(f"\nDry run: {len(references)} targeted Symbiotic vaults would be updated; the vault database, reader-state, and price files were not changed.")
        return

    backup_path = create_backup_path(vault_db_path, now_)
    shutil.copy2(vault_db_path, backup_path)
    vault_db.write(vault_db_path)
    print(f"\nUpdated {len(references)} targeted Symbiotic vaults. Metadata backup: {backup_path}")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, ValueError):
        logger.exception("Symbiotic migration failed")
        raise
