#!/usr/bin/env python3
"""Reclassify persisted pToken metadata for the vault JSON export.

The BTC and HOOD pTokens were initially discovered as generic ERC-7540 vaults
and later incorrectly attributed to Arcus. Their issuer is currently not yet
identified. This migration rebuilds only the two reviewed Robinhood Chain rows
with the address-scoped pToken reader, preserving observed discovery history.
It never changes reader-state or raw/cleaned price Parquet files.

Usage:

.. code-block:: shell

    source .local-test.env && DRY_RUN=true \\
        poetry run python scripts/erc-4626/migrate-ptoken-vault-metadata.py

    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-ptoken-vault-metadata.py

``JSON_RPC_ROBINHOOD`` is required. ``VAULT_DB_PATH`` optionally selects the
metadata pickle and ``DRY_RUN`` defaults to ``true``.
"""

import dataclasses
import datetime
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_CHAIN_ID, PTOKEN_HOOD_3X_LONG_VAULT
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Exact reviewed pToken vaults eligible for this metadata-only migration.
REVIEWED_PTOKEN_VAULT_SPECS: Final[tuple[VaultSpec, ...]] = (
    VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_BTC_3X_LONG_VAULT),
    VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_HOOD_3X_LONG_VAULT),
)


@dataclass(slots=True, frozen=True)
class PTokenMetadataMigrationResult:
    """Summarise one targeted pToken metadata migration.

    :param inspected_rows:
        Number of reviewed pToken rows inspected.
    :param migrated_rows:
        Number of metadata rows rebuilt or that would be rebuilt.
    :param seeded_leads:
        Number of missing target-only lead records restored.
    """

    #: Number of reviewed target rows inspected.
    inspected_rows: int

    #: Number of target rows whose metadata was rebuilt.
    migrated_rows: int

    #: Number of missing target lead records restored.
    seeded_leads: int


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read one strictly validated boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the environment variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If the supplied value is not a recognised boolean literal.
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


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-existing pToken metadata backup path.

    :param vault_db_path:
        Metadata database about to be updated.
    :return:
        A non-existing sibling path for its backup.
    """

    backup_path = Path(f"{vault_db_path}.bak-ptoken-metadata")
    backup_index = 1
    while backup_path.exists():
        backup_path = Path(f"{vault_db_path}.bak-ptoken-metadata.{backup_index}")
        backup_index += 1
    return backup_path


def get_existing_detection(row: VaultRow, spec: VaultSpec) -> ERC4262VaultDetection:
    """Extract persisted detection data while retaining discovery history.

    :param row:
        Existing metadata row for a reviewed pToken.
    :param spec:
        Expected vault identity.
    :return:
        Persisted detector data matching ``spec``.
    :raises ValueError:
        If the existing record cannot safely preserve its discovery history.
    """

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        raise ValueError(f"pToken target {spec} has no persisted ERC-4626 detection")
    if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
        raise ValueError(f"pToken target {spec} has mismatched persisted detection {detection.get_spec()}")
    return detection


def create_reclassified_detection(existing: ERC4262VaultDetection, features: set[ERC4626Feature], updated_at: datetime.datetime) -> ERC4262VaultDetection:
    """Replace the protocol classification without changing observed activity.

    :param existing:
        Persisted discovery result for one reviewed pToken.
    :param features:
        Current address-scoped classification.
    :param updated_at:
        Naive UTC metadata refresh timestamp.
    :return:
        Updated detection retaining first-seen blocks and event counts.
    :raises ValueError:
        If the target no longer has the reviewed pToken classification.
    """

    if ERC4626Feature.ptoken_like not in features:
        raise ValueError(f"pToken target {existing.get_spec()} did not classify as pToken: {features}")
    return dataclasses.replace(existing, features=features, updated_at=updated_at)


def needs_metadata_refresh(row: VaultRow, detection: ERC4262VaultDetection) -> bool:
    """Determine whether a row lacks the corrected pToken metadata.

    :param row:
        Existing vault metadata row.
    :param detection:
        Persisted detector data for the same row.
    :return:
        ``True`` when the metadata needs rebuilding.
    """

    return row.get("Protocol") != "pToken" or ERC4626Feature.ptoken_like not in detection.features or ERC4626Feature.ptoken_like not in row.get("features", set()) or row.get("_manager_name") is not None or not str(row.get("_description", "")).startswith("Currently not yet identified")


def create_lead_from_detection(detection: ERC4262VaultDetection) -> PotentialVaultMatch:
    """Restore one missing pToken lead from its persisted detection.

    :param detection:
        Persisted target discovery data.
    :return:
        Equivalent lead without new event discovery.
    """

    return PotentialVaultMatch(
        chain=detection.chain,
        address=HexAddress(detection.address.lower()),
        first_seen_at_block=detection.first_seen_at_block,
        first_seen_at=detection.first_seen_at,
        deposit_count=detection.deposit_count,
        withdrawal_count=detection.redeem_count,
        configuration_count=detection.configuration_count,
    )


def migrate_ptoken_metadata(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, *, dry_run: bool, updated_at: datetime.datetime) -> PTokenMetadataMigrationResult:
    """Rebuild only the reviewed pToken metadata rows.

    All target validation and RPC reads complete before in-memory mutation, so
    a failed target cannot leave the production pickle partially updated.

    :param web3:
        Robinhood Chain Web3 client used for classification and scanner rows.
    :param vault_db:
        Existing vault metadata database.
    :param token_cache:
        Temporary ERC-20 metadata cache for scanner row construction.
    :param dry_run:
        Do not mutate ``vault_db`` when ``True``.
    :param updated_at:
        Naive UTC metadata refresh timestamp.
    :return:
        Target-only migration counters.
    :raises ValueError:
        If the RPC chain or persisted target state is unexpected.
    """

    if web3.eth.chain_id != PTOKEN_CHAIN_ID:
        raise ValueError(f"JSON_RPC_ROBINHOOD returned chain {web3.eth.chain_id}, expected {PTOKEN_CHAIN_ID}")

    replacements: dict[VaultSpec, VaultRow] = {}
    restored_leads: dict[VaultSpec, PotentialVaultMatch] = {}
    report_rows: list[dict[str, object]] = []
    for spec in REVIEWED_PTOKEN_VAULT_SPECS:
        row = vault_db.rows.get(spec)
        if row is None:
            raise ValueError(f"pToken target {spec} is missing from metadata; do not reset whole-chain discovery")
        existing_detection = get_existing_detection(row, spec)
        detection = create_reclassified_detection(existing_detection, detect_vault_features(web3, spec.vault_address, verbose=False), updated_at)
        refresh = needs_metadata_refresh(row, existing_detection)
        if refresh:
            rebuilt_row = create_vault_scan_record(web3, detection, web3.eth.block_number, token_cache)
            if rebuilt_row.get("Protocol") != "pToken":
                raise ValueError(f"pToken target {spec} rebuilt with unexpected protocol {rebuilt_row.get('Protocol')!r}")
            replacements[spec] = rebuilt_row
        if spec not in vault_db.leads:
            restored_leads[spec] = create_lead_from_detection(existing_detection)
        report_rows.append({"address": spec.vault_address, "previous protocol": row.get("Protocol", "<missing>"), "action": "refresh metadata" if refresh else "already current"})

    print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))
    result = PTokenMetadataMigrationResult(len(REVIEWED_PTOKEN_VAULT_SPECS), len(replacements), len(restored_leads))
    if not dry_run:
        vault_db.rows.update(replacements)
        vault_db.leads.update(restored_leads)
    return result


def main() -> None:
    """Run the pToken metadata migration from environment configuration.

    :return:
        ``None`` after reporting or writing the target-only repair.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/migrate-ptoken-vault-metadata.log"))
    json_rpc_url = os.environ.get("JSON_RPC_ROBINHOOD")
    if not json_rpc_url:
        message = "JSON_RPC_ROBINHOOD is required for the pToken metadata migration"
        raise ValueError(message)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    if not vault_db_path.exists():
        raise ValueError(f"Vault metadata database does not exist: {vault_db_path}")

    dry_run = parse_bool_env("DRY_RUN", default=True)
    vault_db = VaultDatabase.read(vault_db_path)
    with tempfile.TemporaryDirectory(prefix="ptoken-metadata-token-cache-") as cache_directory:
        result = migrate_ptoken_metadata(create_multi_provider_web3(json_rpc_url), vault_db, TokenDiskCache(Path(cache_directory) / "tokens.sqlite"), dry_run=dry_run, updated_at=native_datetime_utc_now())
    if dry_run:
        print(f"Dry run: {result.migrated_rows} pToken metadata rows and {result.seeded_leads} target leads would be updated. Reader state and price files are unchanged.")
        return
    if result.migrated_rows == 0 and result.seeded_leads == 0:
        print("pToken metadata is already current; no files changed.")
        return
    backup_path = create_backup_path(vault_db_path)
    shutil.copy2(vault_db_path, backup_path)
    vault_db.write(vault_db_path)
    print(f"Updated {result.migrated_rows} pToken metadata rows and restored {result.seeded_leads} target leads. Metadata backup: {backup_path}")


if __name__ == "__main__":
    main()
