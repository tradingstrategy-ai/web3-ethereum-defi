"""Tests for the fixed-scope private Lagoon metadata migration."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import LAGOON_PRIVATE_VAULT_ALLOWLIST, PRIVATE_LAGOON_VAULT_NOTE
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.flag import MISSING_IN_PROTOCOL_FRONTEND, VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_REVIEWED_VAULT_COUNT = len(LAGOON_PRIVATE_VAULT_ALLOWLIST)


def load_migration_module() -> ModuleType:
    """Load the private Lagoon migration script as a test module.

    :return:
        Imported migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-lagoon-private-vaults.py"
    spec = importlib.util.spec_from_file_location("migrate_lagoon_private_vaults", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec) -> ERC4262VaultDetection:
    """Create a Lagoon detection envelope for a migration fixture.

    :param spec:
        Chain and vault address represented by the detection.
    :return:
        Persisted scanner detection with the Lagoon feature.
    """

    timestamp = datetime.datetime(2026, 9, 28)  # noqa: DTZ001 - Repository convention is naive UTC.
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features={ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like},
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_database(*, custom_note_spec: VaultSpec | None = None) -> tuple[VaultDatabase, VaultSpec]:
    """Create all reviewed Lagoon rows plus one unrelated row.

    :param custom_note_spec:
        Optional reviewed row whose manual note must be preserved.
    :return:
        Fixture database and unrelated row specification.
    """

    rows = {}
    for index, (chain_id, address) in enumerate(sorted(LAGOON_PRIVATE_VAULT_ALLOWLIST)):
        spec = VaultSpec(chain_id, address)
        rows[spec] = {
            "Name": f"Private Lagoon {index}",
            "Protocol": "Lagoon Finance",
            "features": {ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like},
            "_detection_data": create_detection(spec),
            "_deposit_permission": VaultDepositPermission.unknown.value,
            "_flags": {VaultFlag.unofficial, VaultFlag.long_duration},
            "_notes": "Manual reviewed note" if spec == custom_note_spec else MISSING_IN_PROTOCOL_FRONTEND,
        }

    unrelated_spec = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    rows[unrelated_spec] = {
        "Name": "Unrelated vault",
        "Protocol": "ERC-4626",
        "_flags": {VaultFlag.unofficial},
        "_notes": "Unrelated note",
    }
    return VaultDatabase(rows=rows), unrelated_spec


def create_vault_factory(permission_by_spec: dict[VaultSpec, bool]):
    """Create an injected adapter factory with deterministic live policy reads.

    :param permission_by_spec:
        Vault-wide whitelist-mode result by reviewed specification.
    :return:
        Factory compatible with the migration entry point.
    """

    def vault_factory(_web3: object, detection: ERC4262VaultDetection) -> object:
        spec = VaultSpec(detection.chain, detection.address)

        def is_whitelisted_deposit() -> bool:
            """Return the fixture's current vault-wide access mode."""

            return permission_by_spec[spec]

        return SimpleNamespace(address=detection.address, is_whitelisted_deposit=is_whitelisted_deposit)

    return vault_factory


def test_private_lagoon_migration_dry_run_is_exact_and_non_mutating(tmp_path: Path) -> None:
    """Plan all reviewed repairs without changing the pickle or making a backup."""

    migration = load_migration_module()
    reviewed_specs = migration.get_reviewed_specs()
    custom_note_spec = reviewed_specs[0]
    vault_db, unrelated_spec = create_database(custom_note_spec=custom_note_spec)
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    original_bytes = vault_db_path.read_bytes()
    permission_by_spec = {spec: index % 2 == 0 for index, spec in enumerate(reviewed_specs)}

    result = migration.migrate_lagoon_private_vaults(
        vault_db_path,
        dry_run=True,
        web3_by_chain={1: object(), 42161: object()},
        vault_factory=create_vault_factory(permission_by_spec),
    )

    assert result.inspected_rows == EXPECTED_REVIEWED_VAULT_COUNT
    assert len(result.updates) == EXPECTED_REVIEWED_VAULT_COUNT
    assert {update.spec for update in result.updates} == set(reviewed_specs)
    assert vault_db_path.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.bak-lagoon-private-vaults*")) == []

    unchanged = VaultDatabase.read(vault_db_path)
    assert unchanged.rows[custom_note_spec]["_notes"] == "Manual reviewed note"
    assert unchanged.rows[unrelated_spec]["_flags"] == {VaultFlag.unofficial}


def test_private_lagoon_migration_writes_backup_and_preserves_unrelated_metadata(tmp_path: Path) -> None:  # noqa: PLR0914 - End-to-end migration assertions retain each artefact.
    """Persist only reviewed fields, preserve other flags and support idempotent reruns."""

    migration = load_migration_module()
    reviewed_specs = migration.get_reviewed_specs()
    custom_note_spec = reviewed_specs[0]
    vault_db, unrelated_spec = create_database(custom_note_spec=custom_note_spec)
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    original_bytes = vault_db_path.read_bytes()
    permission_by_spec = {spec: index % 2 == 0 for index, spec in enumerate(reviewed_specs)}
    vault_factory = create_vault_factory(permission_by_spec)

    result = migration.migrate_lagoon_private_vaults(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object(), 42161: object()},
        vault_factory=vault_factory,
    )

    assert len(result.updates) == EXPECTED_REVIEWED_VAULT_COUNT
    backup_path = tmp_path / "vault-metadata-db.pickle.bak-lagoon-private-vaults"
    assert backup_path.read_bytes() == original_bytes

    migrated = VaultDatabase.read(vault_db_path)
    for spec in reviewed_specs:
        row = migrated.rows[spec]
        expected_permission = VaultDepositPermission.whitelisted.value if permission_by_spec[spec] else VaultDepositPermission.permissionless.value
        assert row["_deposit_permission"] == expected_permission
        assert row["_flags"] == {VaultFlag.long_duration}
        expected_note = "Manual reviewed note" if spec == custom_note_spec else PRIVATE_LAGOON_VAULT_NOTE
        assert row["_notes"] == expected_note
    assert migrated.rows[unrelated_spec] == vault_db.rows[unrelated_spec]

    rerun = migration.migrate_lagoon_private_vaults(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object(), 42161: object()},
        vault_factory=vault_factory,
    )
    assert rerun.updates == ()
    assert not (tmp_path / "vault-metadata-db.pickle.bak-lagoon-private-vaults.1").exists()


def test_private_lagoon_migration_aborts_on_unknown_permission(tmp_path: Path) -> None:
    """Do not write any row when a reviewed live policy remains inconclusive."""

    migration = load_migration_module()
    vault_db, _ = create_database()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    original_bytes = vault_db_path.read_bytes()

    def unresolved_factory(_web3: object, detection: ERC4262VaultDetection) -> object:
        def is_whitelisted_deposit() -> bool:
            """Model an adapter that cannot prove its current policy."""

            raise NotImplementedError

        return SimpleNamespace(address=detection.address, is_whitelisted_deposit=is_whitelisted_deposit)

    with pytest.raises(RuntimeError, match="Could not determine current deposit permission"):
        migration.migrate_lagoon_private_vaults(
            vault_db_path,
            dry_run=False,
            web3_by_chain={1: object(), 42161: object()},
            vault_factory=unresolved_factory,
        )

    assert vault_db_path.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.bak-lagoon-private-vaults*")) == []
