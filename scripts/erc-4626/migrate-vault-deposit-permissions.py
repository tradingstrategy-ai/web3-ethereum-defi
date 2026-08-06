"""Refresh cached vault deposit-permission classifications from live adapters.

The vault metadata database persists ``_deposit_permission`` for JSON export.
When an adapter's canonical permission read changes, rows created before that
change keep their old value until metadata is refreshed. This migration reads
the existing detection envelope, reconstructs the current adapter, and updates
only the persisted permission classification.

The command does not discover leads, alter prices, reader state, or any other
vault metadata. It is dry-run-first and creates a non-overwriting backup before
writing the pickle.

Usage:

.. code-block:: shell

    # Review live permission differences without writing (the default)
    source .local-test.env && poetry run python scripts/erc-4626/migrate-vault-deposit-permissions.py

    # Persist reviewed differences
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-vault-deposit-permissions.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``.
- ``DRY_RUN``: Set to ``false`` to write changes. Defaults to ``true``.
- ``JSON_RPC_<CHAIN>``: RPC URL for every EVM chain represented in the cache.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.chain import EVM_BLOCK_TIMES
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

VaultFactory = Callable[[Web3, ERC4262VaultDetection], VaultBase | None]


@dataclass(slots=True, frozen=True)
class VaultDepositPermissionUpdate:
    """One cached vault permission classification requiring correction."""

    #: Chain and vault address identifying the metadata row.
    spec: VaultSpec

    #: Cached human-readable vault name.
    name: str

    #: Cached protocol label.
    protocol: str

    #: Existing persisted permission classification.
    old_permission: str

    #: Current adapter-derived permission classification.
    new_permission: str


@dataclass(slots=True, frozen=True)
class VaultDepositPermissionMigrationResult:
    """Summary of a cached vault deposit-permission migration."""

    #: Total metadata rows inspected.
    inspected_rows: int

    #: EVM rows with a compatible stored detection envelope.
    candidate_rows: int

    #: Rows whose persisted permission classification was or would be updated.
    updated_rows: tuple[VaultDepositPermissionUpdate, ...]

    #: Native or synthetic-chain rows excluded from adapter reads.
    skipped_non_evm_rows: int

    #: Rows whose detection does not construct a supported vault adapter.
    skipped_unsupported_rows: int

    #: Rows whose live read is inconclusive and therefore remain unchanged.
    unresolved_rows: int


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse an explicit boolean environment value.

    :param value:
        Environment value, or ``None`` when unset.
    :param default:
        Value to use when ``value`` is unset.
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


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for the metadata cache.

    :param vault_db_path:
        Existing metadata cache to protect.
    :return:
        Sibling backup path that does not already exist.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-deposit-permission")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def create_vault_for_permission_read(web3: Web3, detection: ERC4262VaultDetection) -> VaultBase | None:
    """Recreate the current adapter needed for a permission read.

    A cached feature envelope can outlive an adapter's static product registry.
    In particular, Asseto rejects a token that is not a supported product. Such
    a row cannot provide a deposit-permission answer and is skipped like other
    unsupported adapters; unrelated construction errors still propagate.

    :param web3:
        Verified chain connection for the detection.
    :param detection:
        Persisted vault feature envelope.
    :return:
        Current adapter, or ``None`` when the envelope is unsupported.
    """

    try:
        return create_vault_instance(
            web3,
            detection.address,
            detection.features,
            default_block_identifier="latest",
        )
    except RuntimeError as error:
        if not str(error).startswith("Unsupported Asseto product:"):
            raise
        logger.warning("Skipping unsupported cached Asseto product %s: %s", detection.address, error)
        return None


def create_web3_by_chain(candidate_specs: Iterable[VaultSpec]) -> dict[int, Web3]:
    """Create one verified JSON-RPC connection per candidate chain.

    :param candidate_specs:
        EVM vaults selected from the metadata cache.
    :return:
        Chain id mapped to a live multi-provider Web3 client.
    """

    chain_ids = sorted({spec.chain_id for spec in candidate_specs})
    return {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id), expected_chain_id=chain_id) for chain_id in chain_ids}


def select_permission_candidates(vault_db: VaultDatabase) -> tuple[list[tuple[VaultSpec, VaultRow]], int]:
    """Select cached EVM rows that retain a usable detection envelope.

    :param vault_db:
        Loaded persisted metadata cache.
    :return:
        Candidate rows and count of non-EVM or non-detection rows excluded.
    """

    candidate_rows: list[tuple[VaultSpec, VaultRow]] = []
    skipped_rows = 0
    for spec, row in vault_db.rows.items():
        detection = row.get("_detection_data")
        if spec.chain_id not in EVM_BLOCK_TIMES or not isinstance(detection, ERC4262VaultDetection):
            skipped_rows += 1
            continue
        if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
            raise RuntimeError(f"Stored detection does not match vault row {spec}")
        candidate_rows.append((spec, row))
    return candidate_rows, skipped_rows


def fetch_permission_updates(
    candidate_rows: Iterable[tuple[VaultSpec, VaultRow]],
    web3_by_chain: Mapping[int, Web3],
    vault_factory: VaultFactory,
) -> tuple[list[VaultDepositPermissionUpdate], int, int]:
    """Read live permission policy and collect only safe differences.

    A transient or unsupported adapter read becomes ``unknown`` through the
    scanner helper. Such a result must not overwrite an existing explicit
    classification during a repair, because it would replace evidence with an
    inconclusive transport outcome.

    :param candidate_rows:
        EVM cache rows selected for live reads.
    :param web3_by_chain:
        Verified connections by chain id.
    :param vault_factory:
        Current adapter constructor, injectable for tests.
    :return:
        Changes, unsupported adapter count, and unresolved-read count.
    """

    updates: list[VaultDepositPermissionUpdate] = []
    skipped_unsupported_rows = 0
    unresolved_rows = 0
    for spec, row in tqdm(candidate_rows, desc="Reading vault deposit permissions"):
        detection = row["_detection_data"]
        assert isinstance(detection, ERC4262VaultDetection)
        vault = vault_factory(web3_by_chain[spec.chain_id], detection)
        if vault is None:
            skipped_unsupported_rows += 1
            continue

        old_permission = row.get("_deposit_permission", VaultDepositPermission.unknown.value)
        try:
            old_permission = VaultDepositPermission(old_permission).value
        except (TypeError, ValueError):
            old_permission = VaultDepositPermission.unknown.value
        new_permission = fetch_deposit_permission(vault).value
        if new_permission == VaultDepositPermission.unknown.value:
            unresolved_rows += 1
            continue
        if old_permission == new_permission:
            continue

        updates.append(
            VaultDepositPermissionUpdate(
                spec=spec,
                name=str(row.get("Name", "")),
                protocol=str(row.get("Protocol", "")),
                old_permission=old_permission,
                new_permission=new_permission,
            )
        )

    return updates, skipped_unsupported_rows, unresolved_rows


def apply_permission_updates(vault_db: VaultDatabase, updates: Iterable[VaultDepositPermissionUpdate]) -> None:
    """Apply permission updates without altering other metadata fields.

    :param vault_db:
        Loaded metadata cache to mutate in memory.
    :param updates:
        Reviewed live permission differences.
    :return:
        None after updating the in-memory cache.
    """

    for update in updates:
        row = vault_db.rows[update.spec].copy()
        row["_deposit_permission"] = update.new_permission
        vault_db.rows[update.spec] = row


def migrate_vault_deposit_permissions(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
    web3_by_chain: Mapping[int, Web3] | None = None,
    vault_factory: VaultFactory = create_vault_for_permission_read,
) -> VaultDepositPermissionMigrationResult:
    """Refresh cached deposit permissions without a discovery or price scan.

    Every selected adapter is queried before a backup or pickle write. An
    unexpected adapter failure therefore aborts before any partial result can
    be persisted.

    :param vault_db_path:
        Persisted vault metadata cache to inspect or update.
    :param dry_run:
        Report differences without writing when ``True``.
    :param web3_by_chain:
        Optional verified clients, primarily for tests.
    :param vault_factory:
        Current adapter constructor, injectable for tests.
    :return:
        Counts and permission updates that were or would be applied.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    candidate_rows, skipped_non_evm_rows = select_permission_candidates(vault_db)
    web3_by_chain = dict(web3_by_chain) if web3_by_chain is not None else create_web3_by_chain(spec for spec, _ in candidate_rows)

    missing_chains = sorted({spec.chain_id for spec, _ in candidate_rows}.difference(web3_by_chain))
    if missing_chains:
        raise ValueError(f"Missing Web3 clients for deposit-permission migration chains: {missing_chains}")

    updates, skipped_unsupported_rows, unresolved_rows = fetch_permission_updates(candidate_rows, web3_by_chain, vault_factory)
    result = VaultDepositPermissionMigrationResult(
        inspected_rows=len(vault_db.rows),
        candidate_rows=len(candidate_rows),
        updated_rows=tuple(updates),
        skipped_non_evm_rows=skipped_non_evm_rows,
        skipped_unsupported_rows=skipped_unsupported_rows,
        unresolved_rows=unresolved_rows,
    )

    if result.updated_rows:
        print(
            tabulate(
                [
                    [
                        update.spec.chain_id,
                        update.spec.vault_address,
                        update.name,
                        update.protocol,
                        update.old_permission,
                        update.new_permission,
                    ]
                    for update in result.updated_rows
                ],
                headers=["chain", "address", "name", "protocol", "old permission", "new permission"],
                tablefmt="simple",
            )
        )

    if dry_run:
        logger.info("DRY RUN: would update %d vault deposit permissions in %s", len(result.updated_rows), vault_db_path)
        return result

    if not result.updated_rows:
        logger.info("No cached vault deposit permissions need repair in %s", vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_permission_updates(vault_db, result.updated_rows)
    vault_db.write(vault_db_path)
    logger.info("Updated %d vault deposit permissions in %s", len(result.updated_rows), vault_db_path)
    return result


def main() -> None:
    """Run the vault deposit-permission migration from environment configuration.

    :return:
        None. Raises when the metadata cache or a required RPC is unavailable.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE)))).expanduser()
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")

    result = migrate_vault_deposit_permissions(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows, selected {result.candidate_rows:,} EVM rows, updated {len(result.updated_rows):,}, skipped {result.skipped_non_evm_rows:,} non-EVM rows, skipped {result.skipped_unsupported_rows:,} unsupported rows, and left {result.unresolved_rows:,} unresolved.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.updated_rows:
        print(f"Saved migrated vault metadata to {vault_db_path}")


if __name__ == "__main__":
    main()
