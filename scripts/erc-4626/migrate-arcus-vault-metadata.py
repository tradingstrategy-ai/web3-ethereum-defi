#!/usr/bin/env python3
"""Reclassify persisted Arcus pToken metadata for the vault JSON export.

PR #1473 added Arcus pToken detection after the Robinhood Chain scanner had
already discovered the reviewed BTC and HOOD pTokens. Incremental discovery
does not revisit existing metadata rows, so they remain labelled ``<unknown
ERC-7540>`` until this targeted repair rebuilds their metadata with the Arcus
reader.

The migration only updates the two reviewed metadata rows. It preserves their
observed discovery blocks and event counts, and never changes reader-state or
raw/cleaned price Parquet files. Existing price history is already sufficient
for the JSON exporter. Run the normal export after applying this migration.

Usage:

.. code-block:: shell

    source .local-test.env && DRY_RUN=true \\
        poetry run python scripts/erc-4626/migrate-arcus-vault-metadata.py

    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-arcus-vault-metadata.py

Environment variables:

- ``JSON_RPC_ROBINHOOD``: Robinhood Chain JSON-RPC endpoint. Required to
  verify the current Arcus classification and refresh metadata.
- ``VAULT_DB_PATH``: Optional metadata pickle path. Defaults to the production
  vault metadata database.
- ``DRY_RUN``: Report affected rows without writing. Defaults to ``true``.
- ``LOG_LEVEL``: Optional log level. Defaults to ``info``.
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
from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_CHAIN_ID, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Exact reviewed Arcus pTokens that PR #1473 supports.
REVIEWED_ARCUS_VAULT_SPECS: Final[tuple[VaultSpec, ...]] = (
    VaultSpec(ARCUS_CHAIN_ID, ARCUS_BTC_3X_LONG_VAULT),
    VaultSpec(ARCUS_CHAIN_ID, ARCUS_HOOD_3X_LONG_VAULT),
)


@dataclass(slots=True, frozen=True)
class ArcusMetadataMigrationResult:
    """Summarise one Arcus metadata migration run.

    :param inspected_rows:
        Number of reviewed Arcus rows checked.
    :param migrated_rows:
        Number of stale rows rebuilt, or that would be rebuilt in dry-run mode.
    :param seeded_leads:
        Number of missing target-only lead records restored from metadata.
    """

    #: Number of reviewed Arcus rows inspected.
    inspected_rows: int

    #: Number of stale metadata rows rebuilt.
    migrated_rows: int

    #: Number of target-only leads restored from persisted metadata.
    seeded_leads: int


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read one strictly validated boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If a supplied value is not a recognised boolean literal.
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
    """Choose a unique sibling backup path for a metadata database.

    :param vault_db_path:
        Metadata database about to be updated.
    :return:
        A non-existing path next to ``vault_db_path``.
    """

    backup_path = Path(f"{vault_db_path}.bak-arcus-metadata")
    backup_index = 1
    while backup_path.exists():
        backup_path = Path(f"{vault_db_path}.bak-arcus-metadata.{backup_index}")
        backup_index += 1
    return backup_path


def get_existing_detection(row: VaultRow, spec: VaultSpec) -> ERC4262VaultDetection:
    """Extract and validate the persisted detector data for a reviewed vault.

    The existing detection is the authoritative source for observed discovery
    blocks and flow counts. Reconstructing either from a new current-state
    read could falsify export history.

    :param row:
        Existing metadata row for the reviewed vault.
    :param spec:
        Expected vault database identity.
    :return:
        Persisted detection object matching ``spec``.
    :raises ValueError:
        If a target row has no compatible persisted detection.
    """

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        raise ValueError(f"Arcus target {spec} has no persisted ERC-4626 detection")
    if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
        raise ValueError(f"Arcus target {spec} has mismatched persisted detection {detection.get_spec()}")
    return detection


def create_reclassified_detection(existing: ERC4262VaultDetection, features: set[ERC4626Feature], updated_at: datetime.datetime) -> ERC4262VaultDetection:
    """Preserve observed history while replacing stale classification features.

    :param existing:
        Existing persisted discovery result for one reviewed pToken.
    :param features:
        Current onchain feature classification.
    :param updated_at:
        Naive UTC metadata refresh timestamp.
    :return:
        Updated detection retaining all original discovery information.
    :raises ValueError:
        If the current target no longer has the reviewed Arcus signature.
    """

    if ERC4626Feature.arcus_like not in features:
        raise ValueError(f"Arcus target {existing.get_spec()} did not classify as Arcus: {features}")
    return dataclasses.replace(existing, features=features, updated_at=updated_at)


def needs_metadata_refresh(row: VaultRow, detection: ERC4262VaultDetection) -> bool:
    """Determine whether a persisted record lacks Arcus export metadata.

    :param row:
        Existing metadata row.
    :param detection:
        Its persisted detection result.
    :return:
        ``True`` when rebuilding the row is required.
    """

    return any(
        (
            row.get("Protocol") != "Arcus",
            ERC4626Feature.arcus_like not in detection.features,
            ERC4626Feature.arcus_like not in row.get("features", set()),
            row.get("_manager_name") is not None,
            not all(row.get(field) for field in ("_short_description", "_description", "_notes")),
        )
    )


def create_lead_from_detection(detection: ERC4262VaultDetection) -> PotentialVaultMatch:
    """Restore a missing target lead without altering its observed activity.

    :param detection:
        Persisted Arcus discovery data.
    :return:
        Equivalent lead record for the reviewed pToken.
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


def migrate_arcus_metadata(
    web3: Web3,
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache,
    *,
    dry_run: bool,
    updated_at: datetime.datetime,
) -> ArcusMetadataMigrationResult:
    """Rebuild stale metadata rows for only the reviewed Arcus pTokens.

    All target validation and RPC reads finish before any in-memory database
    mutation. Consequently a failed target does not leave a partially migrated
    production pickle. The caller writes the pickle only after this function
    returns successfully.

    :param web3:
        Robinhood Chain Web3 client used for detection and metadata reads.
    :param vault_db:
        Existing vault metadata database.
    :param token_cache:
        Temporary ERC-20 metadata cache for scanner row construction.
    :param dry_run:
        Do not mutate the supplied database when ``True``.
    :param updated_at:
        Naive UTC refresh timestamp.
    :return:
        Target-only migration counters.
    :raises ValueError:
        If the RPC chain or existing target state is unexpected.
    """

    if web3.eth.chain_id != ARCUS_CHAIN_ID:
        raise ValueError(f"JSON_RPC_ROBINHOOD returned chain {web3.eth.chain_id}, expected {ARCUS_CHAIN_ID}")

    replacements: dict[VaultSpec, VaultRow] = {}
    restored_leads: dict[VaultSpec, PotentialVaultMatch] = {}
    report_rows: list[dict[str, object]] = []

    for spec in REVIEWED_ARCUS_VAULT_SPECS:
        row = vault_db.rows.get(spec)
        if row is None:
            raise ValueError(f"Arcus target {spec} is missing from the metadata database; do not reset whole-chain discovery")

        existing_detection = get_existing_detection(row, spec)
        features = detect_vault_features(web3, spec.vault_address, verbose=False)
        detection = create_reclassified_detection(existing_detection, features, updated_at)
        refresh = needs_metadata_refresh(row, existing_detection)

        if refresh:
            rebuilt_row = create_vault_scan_record(web3, detection, web3.eth.block_number, token_cache)
            if rebuilt_row.get("Protocol") != "Arcus":
                raise ValueError(f"Arcus target {spec} rebuilt with unexpected protocol {rebuilt_row.get('Protocol')!r}")
            replacements[spec] = rebuilt_row

        if spec not in vault_db.leads:
            restored_leads[spec] = create_lead_from_detection(existing_detection)

        report_rows.append(
            {
                "address": spec.vault_address,
                "previous protocol": row.get("Protocol", "<missing>"),
                "action": "refresh metadata" if refresh else "already current",
            }
        )

    print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))

    result = ArcusMetadataMigrationResult(
        inspected_rows=len(REVIEWED_ARCUS_VAULT_SPECS),
        migrated_rows=len(replacements),
        seeded_leads=len(restored_leads),
    )
    if dry_run:
        return result

    vault_db.rows.update(replacements)
    vault_db.leads.update(restored_leads)
    return result


def main() -> None:
    """Run the Arcus metadata migration from the environment configuration.

    :return:
        ``None`` after displaying the dry-run plan or saving the updated
        metadata pickle and backup.
    """

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/migrate-arcus-vault-metadata.log"),
    )
    json_rpc_url = os.environ.get("JSON_RPC_ROBINHOOD")
    if not json_rpc_url:
        message = "JSON_RPC_ROBINHOOD is required for the Arcus metadata migration"
        raise ValueError(message)

    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    if not vault_db_path.exists():
        raise ValueError(f"Vault metadata database does not exist: {vault_db_path}")

    dry_run = parse_bool_env("DRY_RUN", default=True)
    web3 = create_multi_provider_web3(json_rpc_url)
    vault_db = VaultDatabase.read(vault_db_path)

    with tempfile.TemporaryDirectory(prefix="arcus-metadata-token-cache-") as cache_directory:
        result = migrate_arcus_metadata(
            web3,
            vault_db,
            TokenDiskCache(Path(cache_directory) / "tokens.sqlite"),
            dry_run=dry_run,
            updated_at=native_datetime_utc_now(),
        )

    if dry_run:
        print(f"Dry run: {result.migrated_rows} Arcus metadata rows and {result.seeded_leads} target leads would be updated. Reader state and price files are unchanged.")
        return

    if result.migrated_rows == 0 and result.seeded_leads == 0:
        print("Arcus metadata is already current; no files changed.")
        return

    backup_path = create_backup_path(vault_db_path)
    shutil.copy2(vault_db_path, backup_path)
    vault_db.write(vault_db_path)
    print(f"Updated {result.migrated_rows} Arcus metadata rows and restored {result.seeded_leads} target leads. Metadata backup: {backup_path}")


if __name__ == "__main__":
    main()
