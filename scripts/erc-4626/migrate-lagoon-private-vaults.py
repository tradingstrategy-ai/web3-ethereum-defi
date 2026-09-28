#!/usr/bin/env python3
"""Repair listing and deposit-permission metadata for reviewed private Lagoon vaults.

The Lagoon application recognises the eight vaults in this migration's fixed
``REVIEWED_PRIVATE_LAGOON_VAULTS`` scope but omits them from its paginated
public catalogue. Rows scanned before the
address-specific review therefore retain the exclusionary ``unofficial`` flag
and missing-frontend note. Some Ethereum RPC providers also omitted the empty
``data`` field from the v0.5 missing-getter revert, leaving five deposit-policy
values as ``unknown``.

This one-off migration validates the exact reviewed rows, reads their current
onchain deposit policy through the production Lagoon adapter, removes only the
``unofficial`` flag, and replaces only the obsolete missing-frontend note. It
does not discover leads, scan or rewrite prices, alter reader state, or touch
timestamp caches. No historical backfill is needed because all corrected
fields are current metadata rather than time-series observations.

Usage::

    source .local-test.env && DRY_RUN=true \
        poetry run python scripts/erc-4626/migrate-lagoon-private-vaults.py
    source .local-test.env && DRY_RUN=false \
        poetry run python scripts/erc-4626/migrate-lagoon-private-vaults.py

Environment variables:

- ``DRY_RUN``: Report exact changes without writing. Defaults to ``true``.
- ``VAULT_DB_PATH``: Optional metadata pickle path. Defaults to
  ``$PIPELINE_DATA_DIR/vault-metadata-db.pickle``.
- ``JSON_RPC_ETHEREUM`` and ``JSON_RPC_ARBITRUM``: Current-state RPC URLs.
- ``PIPELINE_LOCK_TIMEOUT``: Writer-lock timeout in seconds. Defaults to 60.
- ``LOG_LEVEL``: Console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import LAGOON_PRIVATE_VAULT_ALLOWLIST, PRIVATE_LAGOON_VAULT_NOTE
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.flag import MISSING_IN_PROTOCOL_FRONTEND, VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Canonical protocol label required on every selected metadata row.
LAGOON_PROTOCOL_NAME = "Lagoon Finance"

#: Immutable one-off migration scope reviewed on 2026-09-28.
REVIEWED_PRIVATE_LAGOON_VAULTS: frozenset[tuple[int, str]] = frozenset(
    {
        (1, "0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb"),
        (1, "0x23b27310451f2754de34d9c04aa24e8be367124a"),
        (1, "0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87"),
        (1, "0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594"),
        (1, "0xf10801bcc3deaf467fb8b3dbb7430111822e6dab"),
        (1, "0xfd104766499a3ff60ea85b5c6015ba9e32b8c891"),
        (42161, "0x1723cb57af58efb35a013870c90fcc3d60174a4e"),
        (42161, "0xc047d64dafe9e6ac76508835c17c6719f9278c1c"),
    }
)

VaultFactory = Callable[[Web3, ERC4262VaultDetection], LagoonVault]


@dataclass(slots=True, frozen=True)
class LagoonPrivateVaultUpdate:
    """One reviewed private Lagoon row requiring current metadata repair."""

    #: Chain and address identifying the persisted row.
    spec: VaultSpec

    #: Persisted human-readable vault name.
    name: str

    #: Existing deposit-permission classification.
    old_permission: str

    #: Current onchain deposit-permission classification.
    new_permission: str

    #: Metadata fields whose values differ from the reviewed result.
    changed_fields: tuple[str, ...]

    #: Corrected flag set, preserving every flag except ``unofficial``.
    new_flags: frozenset[VaultFlag | str]

    #: Corrected note, preserving any non-obsolete manual copy.
    new_note: str | None


@dataclass(slots=True, frozen=True)
class LagoonPrivateVaultMigrationResult:
    """Summarise one fixed-scope private Lagoon metadata migration."""

    #: Number of reviewed rows inspected.
    inspected_rows: int

    #: Rows changed or proposed for change.
    updates: tuple[LagoonPrivateVaultUpdate, ...]


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse one strict boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value returned when the variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If the value is not a recognised boolean literal.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting sibling backup path.

    :param vault_db_path:
        Metadata pickle protected by the backup.
    :return:
        First available migration-specific backup path.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-lagoon-private-vaults")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def get_reviewed_specs() -> tuple[VaultSpec, ...]:
    """Return the exact chain-aware private Lagoon migration scope.

    :return:
        Eight reviewed private Lagoon vault specifications.
    """

    if not REVIEWED_PRIVATE_LAGOON_VAULTS.issubset(LAGOON_PRIVATE_VAULT_ALLOWLIST):
        message = "Private Lagoon migration scope contains a vault removed from the reviewed listing allowlist"
        raise RuntimeError(message)
    return tuple(VaultSpec(chain_id, address) for chain_id, address in sorted(REVIEWED_PRIVATE_LAGOON_VAULTS))


def create_web3_by_chain(specs: Iterable[VaultSpec]) -> dict[int, Web3]:
    """Create one verified current-state RPC client per selected chain.

    :param specs:
        Reviewed private Lagoon specifications.
    :return:
        Chain IDs mapped to multi-provider Web3 clients.
    """

    return {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id), expected_chain_id=chain_id) for chain_id in sorted({spec.chain_id for spec in specs})}


def create_lagoon_vault(web3: Web3, detection: ERC4262VaultDetection) -> LagoonVault:
    """Reconstruct the production Lagoon adapter from persisted detection.

    :param web3:
        Current-state chain connection.
    :param detection:
        Persisted detection envelope for the reviewed row.
    :return:
        Lagoon adapter pinned to the latest block.
    :raises RuntimeError:
        If persisted features no longer construct a Lagoon adapter.
    """

    vault = create_vault_instance(
        web3,
        detection.address,
        detection.features,
        default_block_identifier="latest",
    )
    if not isinstance(vault, LagoonVault):
        raise RuntimeError(f"Reviewed Lagoon row {detection.chain}-{detection.address} constructed {type(vault).__name__}")
    return vault


def _normalise_permission(value: object) -> str:
    """Normalise a persisted deposit-permission value.

    :param value:
        Stored enum value or legacy/missing object.
    :return:
        JSON-compatible permission string.
    """

    try:
        return VaultDepositPermission(value).value
    except (TypeError, ValueError):
        return VaultDepositPermission.unknown.value


def collect_lagoon_private_vault_updates(
    vault_db: VaultDatabase,
    web3_by_chain: Mapping[int, Web3],
    *,
    vault_factory: VaultFactory = create_lagoon_vault,
) -> tuple[LagoonPrivateVaultUpdate, ...]:
    """Validate reviewed rows and collect live metadata corrections.

    All rows and RPC clients are validated before any update is applied. An
    inconclusive permission read aborts the migration rather than replacing
    reviewed metadata with ``unknown``.

    :param vault_db:
        Existing vault metadata database.
    :param web3_by_chain:
        Verified current-state clients by chain ID.
    :param vault_factory:
        Lagoon adapter constructor, injectable for tests.
    :return:
        Changed row plans in deterministic chain-address order.
    :raises ValueError:
        If a reviewed row, chain client, protocol, or Lagoon feature is absent.
    :raises RuntimeError:
        If a live permission read remains inconclusive.
    """

    reviewed_specs = get_reviewed_specs()
    missing_specs = tuple(spec for spec in reviewed_specs if spec not in vault_db.rows)
    if missing_specs:
        missing = ", ".join(spec.as_string_id() for spec in missing_specs)
        raise ValueError(f"Private Lagoon migration is missing reviewed rows: {missing}")

    missing_chains = sorted({spec.chain_id for spec in reviewed_specs}.difference(web3_by_chain))
    if missing_chains:
        raise ValueError(f"Private Lagoon migration is missing Web3 clients for chains: {missing_chains}")

    for spec in reviewed_specs:
        row = vault_db.rows[spec]
        detection = row.get("_detection_data")
        if row.get("Protocol") != LAGOON_PROTOCOL_NAME:
            raise ValueError(f"Private Lagoon migration found unexpected protocol for {spec}: {row.get('Protocol')!r}")
        if not isinstance(detection, ERC4262VaultDetection) or detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
            raise ValueError(f"Private Lagoon migration found invalid detection data for {spec}")
        if ERC4626Feature.lagoon_like not in detection.features:
            raise ValueError(f"Private Lagoon migration found a non-Lagoon detection for {spec}")

    updates: list[LagoonPrivateVaultUpdate] = []
    for spec in tqdm(reviewed_specs, desc="Reading private Lagoon deposit permissions"):
        row = vault_db.rows[spec]
        detection = row["_detection_data"]
        assert isinstance(detection, ERC4262VaultDetection)
        vault = vault_factory(web3_by_chain[spec.chain_id], detection)
        new_permission = fetch_deposit_permission(vault).value
        if new_permission == VaultDepositPermission.unknown.value:
            raise RuntimeError(f"Could not determine current deposit permission for reviewed private Lagoon vault {spec}")

        old_permission = _normalise_permission(row.get("_deposit_permission"))
        old_flags = set(row.get("_flags") or set())
        new_flags = frozenset(flag for flag in old_flags if flag not in {VaultFlag.unofficial, VaultFlag.unofficial.value})
        old_note = row.get("_notes")
        new_note = PRIVATE_LAGOON_VAULT_NOTE if old_note in {None, MISSING_IN_PROTOCOL_FRONTEND} else old_note
        changed_fields = tuple(
            field
            for field, old_value, new_value in (
                ("_deposit_permission", old_permission, new_permission),
                ("_flags", frozenset(old_flags), new_flags),
                ("_notes", old_note, new_note),
            )
            if old_value != new_value
        )
        if changed_fields:
            updates.append(
                LagoonPrivateVaultUpdate(
                    spec=spec,
                    name=str(row.get("Name", "")),
                    old_permission=old_permission,
                    new_permission=new_permission,
                    changed_fields=changed_fields,
                    new_flags=new_flags,
                    new_note=new_note,
                )
            )

    return tuple(updates)


def apply_lagoon_private_vault_updates(vault_db: VaultDatabase, updates: Iterable[LagoonPrivateVaultUpdate]) -> None:
    """Apply reviewed metadata changes without altering unrelated fields.

    :param vault_db:
        Metadata database to mutate in memory.
    :param updates:
        Fully validated update plans.
    :return:
        None after applying every selected change.
    """

    for update in updates:
        row: VaultRow = vault_db.rows[update.spec].copy()
        if "_deposit_permission" in update.changed_fields:
            row["_deposit_permission"] = update.new_permission
        if "_flags" in update.changed_fields:
            row["_flags"] = set(update.new_flags)
        if "_notes" in update.changed_fields:
            row["_notes"] = update.new_note
        vault_db.rows[update.spec] = row


def migrate_lagoon_private_vaults(
    vault_db_path: Path,
    *,
    dry_run: bool,
    web3_by_chain: Mapping[int, Web3] | None = None,
    vault_factory: VaultFactory = create_lagoon_vault,
) -> LagoonPrivateVaultMigrationResult:
    """Repair the fixed private Lagoon scope in one atomic metadata write.

    :param vault_db_path:
        Existing metadata pickle to inspect or update.
    :param dry_run:
        Report changes without creating a backup or writing when ``True``.
    :param web3_by_chain:
        Optional verified clients, primarily for tests.
    :param vault_factory:
        Lagoon adapter constructor, injectable for tests.
    :return:
        Reviewed row and update summary.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    reviewed_specs = get_reviewed_specs()
    web3_by_chain = dict(web3_by_chain) if web3_by_chain is not None else create_web3_by_chain(reviewed_specs)
    updates = collect_lagoon_private_vault_updates(vault_db, web3_by_chain, vault_factory=vault_factory)
    result = LagoonPrivateVaultMigrationResult(inspected_rows=len(reviewed_specs), updates=updates)

    if dry_run or not updates:
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_lagoon_private_vault_updates(vault_db, updates)
    vault_db.write(vault_db_path)
    return result


def main() -> None:
    """Run the fixed-scope private Lagoon metadata migration.

    Persistent mode holds the shared scanner writer lock from the database
    read through backup and atomic replacement. Dry-run mode is non-mutating.

    :return:
        None after reporting and optionally persisting the migration.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    pipeline_directory = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(pipeline_directory / "vault-metadata-db.pickle"))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock_timeout = float(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60"))
    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=lock_timeout)
    with lock:
        result = migrate_lagoon_private_vaults(vault_db_path, dry_run=dry_run)

    if result.updates:
        report_rows = [
            {
                "Chain": update.spec.chain_id,
                "Address": update.spec.vault_address,
                "Name": update.name,
                "Old permission": update.old_permission,
                "New permission": update.new_permission,
                "Changed fields": ", ".join(update.changed_fields),
            }
            for update in result.updates
        ]
        print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))

    if dry_run:
        outcome = "Dry run; no files changed"
    elif result.updates:
        outcome = f"Written atomically to {vault_db_path}"
    else:
        outcome = "No changes required; metadata pickle not rewritten"
    logger.info(
        "Private Lagoon migration: inspected=%d, updated=%d. %s.",
        result.inspected_rows,
        len(result.updates),
        outcome,
    )


if __name__ == "__main__":
    main()
