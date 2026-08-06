"""Migrate cached ERC-7540 vault rows to automatic Lagoon classification.

The Lagoon detector recognises legacy and recent Lagoon deployments from
their contract interface. Vault rows cached before this detector existed keep
their former ``<unknown ERC-7540>`` classification until they are re-probed.
This migration reads the cached metadata database, re-probes every ERC-7540
candidate (and existing Lagoon row), and persists only rows now identified as
Lagoon.

The migration changes metadata only. It does not touch price Parquet files,
reader state, discovery leads, or any vault history.

Usage:

.. code-block:: shell

    # Inspect the rows that would change; this is the default.
    source .local-test.env && DRY_RUN=true \\
        poetry run python scripts/erc-4626/migrate-lagoon-classification.py

    # Back up and persist the corrected cached classifications.
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-lagoon-classification.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``. Falls
  back to ``VAULT_DB`` and then the production metadata path.
- ``DRY_RUN``: Set to ``true`` to report without writing. Defaults to ``true``.
- ``LOG_LEVEL``: Optional log level. Defaults to ``info``.
- ``JSON_RPC_<CHAIN>``: An RPC URL is required for every chain with an
  ERC-7540 or existing Lagoon row in the metadata cache.
"""

import logging
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from eth_typing import HexAddress
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Canonical scan-record protocol name for Lagoon vaults.
LAGOON_PROTOCOL_NAME = "Lagoon Finance"

#: Stable public protocol slug derived from :data:`LAGOON_PROTOCOL_NAME`.
LAGOON_PROTOCOL_SLUG = "lagoon-finance"

#: Maximum number of changed rows printed by the command.
MAX_UPDATED_ROWS_SHOWN = 100


@dataclass(slots=True, frozen=True)
class LagoonClassificationUpdate:
    """One cached vault classification requiring a Lagoon update."""

    #: Chain and address of the corrected vault.
    spec: VaultSpec

    #: Cached human-readable vault name.
    name: str

    #: Protocol label before automatic reclassification.
    old_protocol: str

    #: Cached feature values before automatic reclassification.
    old_features: frozenset[ERC4626Feature]

    #: Cached and re-probed feature values to persist.
    new_features: frozenset[ERC4626Feature]


@dataclass(slots=True, frozen=True)
class LagoonClassificationMigrationResult:
    """Summary of the cached Lagoon classification migration."""

    #: All metadata rows inspected.
    inspected_rows: int

    #: Cached ERC-7540 or existing Lagoon rows re-probed.
    candidate_rows: int

    #: Candidates recognised as Lagoon by the current automatic detector.
    recognised_lagoon_rows: int

    #: Rows that were or would be updated.
    updated_rows: tuple[LagoonClassificationUpdate, ...]


Detector = Callable[[Web3, HexAddress], set[ERC4626Feature]]


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse an explicit boolean environment value.

    An unset variable uses ``default``. Any value other than a conventional
    boolean spelling raises instead of unexpectedly switching the migration to
    write mode.

    :param value:
        Environment value to parse, or ``None`` when it is unset.
    :param default:
        Value to return for an unset environment variable.
    :return:
        Parsed boolean value.
    """
    if value is None:
        return default

    normalised_value = value.strip().lower()
    if normalised_value in {"1", "true", "yes", "on"}:
        return True
    if normalised_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean environment value, got {value!r}")


def _get_cached_features(row: VaultRow) -> set[ERC4626Feature]:
    """Return the best available persisted feature set for a vault row.

    :param row:
        Cached vault metadata row.

    :return:
        Top-level features, falling back to the detection envelope.
    """
    features = row.get("features")
    if features is not None:
        return set(features)

    detection = row.get("_detection_data")
    if isinstance(detection, ERC4262VaultDetection):
        return set(detection.features)
    return set()


def _is_lagoon_candidate(row: VaultRow) -> bool:
    """Determine whether a cached row needs a Lagoon classification probe.

    Every supported Lagoon vault implements ERC-7540. Existing Lagoon-labelled
    rows are included as well, so their stored feature envelope is refreshed.

    :param row:
        Cached vault metadata row.

    :return:
        ``True`` if the row requires current automatic detection.
    """
    return ERC4626Feature.erc_7540_like in _get_cached_features(row) or row.get("Protocol") == LAGOON_PROTOCOL_NAME


def _format_features(features: Iterable[ERC4626Feature]) -> str:
    """Format feature values for the operator report.

    :param features:
        Features to display.

    :return:
        Comma-separated stable feature values.
    """
    return ", ".join(sorted(feature.value for feature in features))


def apply_lagoon_classification_updates(vault_db: VaultDatabase, updates: Iterable[LagoonClassificationUpdate]) -> None:
    """Apply selected Lagoon metadata updates without altering other row fields.

    :param vault_db:
        In-memory metadata database to update.
    :param updates:
        Re-probed Lagoon classification updates to persist.
    :return:
        ``None`` after updating the in-memory database.
    """
    for update in updates:
        row = vault_db.rows[update.spec].copy()
        features = set(update.new_features)
        row["features"] = features
        row["Features"] = ", ".join(sorted(feature.name for feature in features))
        row["Protocol"] = LAGOON_PROTOCOL_NAME
        row["protocol_slug"] = LAGOON_PROTOCOL_SLUG

        detection = row.get("_detection_data")
        if isinstance(detection, ERC4262VaultDetection):
            row["_detection_data"] = replace(detection, features=features)

        vault_db.rows[update.spec] = row


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for the metadata cache.

    :param vault_db_path:
        Existing metadata cache to protect.

    :return:
        Sibling backup path that does not already exist.
    """
    backup_path = vault_db_path.with_suffix(".pickle.bak-lagoon-classification")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def _create_web3_by_chain(candidate_specs: Iterable[VaultSpec]) -> dict[int, Web3]:
    """Create one JSON-RPC client per candidate chain.

    :param candidate_specs:
        Candidate vault specifications from the metadata cache.

    :return:
        Chain id mapped to a configured multi-provider Web3 client.
    """
    chain_ids = sorted({spec.chain_id for spec in candidate_specs})
    return {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id)) for chain_id in chain_ids}


def _collect_lagoon_classification_updates(
    candidate_rows: list[tuple[VaultSpec, VaultRow]],
    web3_by_chain: Mapping[int, Web3],
    detector: Detector,
) -> tuple[int, list[LagoonClassificationUpdate]]:
    """Re-probe candidates and collect the required metadata updates.

    :param candidate_rows:
        Cached ERC-7540 or Lagoon-labelled rows to re-probe.
    :param web3_by_chain:
        Configured RPC clients by candidate chain id.
    :param detector:
        Automatic vault feature detector.
    :return:
        Number of recognised Lagoon rows and updates to apply.
    """
    recognised_lagoon_rows = 0
    updates: list[LagoonClassificationUpdate] = []

    for spec, row in tqdm(candidate_rows, desc="Re-probing Lagoon candidates"):
        old_features = frozenset(_get_cached_features(row))
        try:
            detected_features = detector(web3_by_chain[spec.chain_id], spec.vault_address)
        except (ConnectionError, TimeoutError, ValueError, Web3Exception) as error:
            raise RuntimeError(f"Could not re-probe Lagoon candidate {spec}. No metadata cache was written.") from error

        if ERC4626Feature.lagoon_like not in detected_features:
            continue

        recognised_lagoon_rows += 1
        new_features = old_features | detected_features
        detection = row.get("_detection_data")
        is_current = row.get("Protocol") == LAGOON_PROTOCOL_NAME and row.get("protocol_slug") == LAGOON_PROTOCOL_SLUG and _get_cached_features(row) == new_features and (not isinstance(detection, ERC4262VaultDetection) or detection.features == new_features)
        if is_current:
            continue

        updates.append(
            LagoonClassificationUpdate(
                spec=spec,
                name=str(row.get("Name", "")),
                old_protocol=str(row.get("Protocol", "")),
                old_features=old_features,
                new_features=frozenset(new_features),
            )
        )

    return recognised_lagoon_rows, updates


def migrate_lagoon_classifications(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
    web3_by_chain: Mapping[int, Web3] | None = None,
    detector: Detector | None = None,
) -> LagoonClassificationMigrationResult:
    """Re-probe cached ERC-7540 rows and migrate automatic Lagoon matches.

    Every candidate is probed before the cache is written. A failed probe aborts
    the migration before any write, ensuring an operator cannot accidentally
    persist a partial all-Lagoon repair.

    :param vault_db_path:
        Metadata cache to inspect or update.
    :param dry_run:
        Report matching rows without writing the cache.
    :param web3_by_chain:
        Optional preconfigured clients, primarily for programmatic use and
        tests. Missing candidate chains raise an error.
    :param detector:
        Optional automatic feature detector. Defaults to the production
        multicall detector.
    :return:
        Counts and detailed rows that were or would be updated.
    """
    vault_db = VaultDatabase.read(vault_db_path)
    candidate_rows = [(spec, row) for spec, row in vault_db.rows.items() if _is_lagoon_candidate(row)]
    detector = detector or (lambda web3, address: detect_vault_features(web3, address, verbose=False))
    web3_by_chain = dict(web3_by_chain) if web3_by_chain is not None else _create_web3_by_chain(spec for spec, _ in candidate_rows)

    missing_chains = sorted({spec.chain_id for spec, _ in candidate_rows}.difference(web3_by_chain))
    if missing_chains:
        raise ValueError(f"Missing Web3 clients for Lagoon migration chains: {missing_chains}")

    recognised_lagoon_rows, updates = _collect_lagoon_classification_updates(candidate_rows, web3_by_chain, detector)

    if updates:
        table_rows = [
            [
                update.spec.chain_id,
                update.spec.vault_address,
                update.name,
                update.old_protocol,
                LAGOON_PROTOCOL_NAME,
                _format_features(update.old_features),
                _format_features(update.new_features),
            ]
            for update in updates[:MAX_UPDATED_ROWS_SHOWN]
        ]
        print(tabulate(table_rows, headers=["Chain", "Address", "Name", "Old protocol", "New protocol", "Old features", "New features"], tablefmt="simple"))
        if len(updates) > MAX_UPDATED_ROWS_SHOWN:
            print(f"... {len(updates) - MAX_UPDATED_ROWS_SHOWN:,} more updated rows not shown")

    result = LagoonClassificationMigrationResult(
        inspected_rows=len(vault_db.rows),
        candidate_rows=len(candidate_rows),
        recognised_lagoon_rows=recognised_lagoon_rows,
        updated_rows=tuple(updates),
    )
    if not result.updated_rows:
        logger.info("No cached Lagoon classifications need repair in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would update %d Lagoon classifications in %s", len(result.updated_rows), vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_lagoon_classification_updates(vault_db, result.updated_rows)
    vault_db.write(vault_db_path)
    logger.info("Updated %d Lagoon classifications in %s", len(result.updated_rows), vault_db_path)
    return result


def main() -> None:
    """Run the cached Lagoon classification migration.

    :return:
        ``None``. Raises if the cache is unavailable or a candidate cannot be
        re-probed.
    """
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/migrate-lagoon-classification.log"),
    )
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE)))).expanduser()
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")

    result = migrate_lagoon_classifications(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows, re-probed {result.candidate_rows:,} candidates, recognised {result.recognised_lagoon_rows:,} Lagoon vaults, updated {len(result.updated_rows):,}.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.updated_rows:
        print(f"Saved migrated vault metadata to {vault_db_path}")


if __name__ == "__main__":
    main()
