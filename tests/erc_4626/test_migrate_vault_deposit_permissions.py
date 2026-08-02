"""Tests for the cached vault deposit-permission migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoGateAddressMissing
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the deposit-permission migration script as a test module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-vault-deposit-permissions.py"
    spec = importlib.util.spec_from_file_location("migrate_vault_deposit_permissions", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec) -> ERC4262VaultDetection:
    """Create cached detection metadata for a vault fixture.

    :param spec:
        Vault identity.
    :return:
        Detection envelope matching a metadata cache row.
    """

    timestamp = datetime.datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001 - Repository convention is naive UTC.
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features={ERC4626Feature.morpho_like},
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_row(spec: VaultSpec, permission: str) -> dict:
    """Create one minimal cached permission row.

    :param spec:
        Vault identity.
    :param permission:
        Cached permission classification.
    :return:
        Vault metadata row.
    """

    return {
        "Name": f"Vault {spec.vault_address[-4:]}",
        "Protocol": "Morpho",
        "_detection_data": create_detection(spec),
        "_deposit_permission": permission,
    }


class FakeVault:
    """Minimal adapter substitute exposing one live permission result."""

    def __init__(self, address: str, *, whitelisted: bool | None) -> None:
        self.address = address
        self.whitelisted = whitelisted

    def is_whitelisted_deposit(self) -> bool:
        """Return the fixture policy or simulate an unsupported read."""

        if self.whitelisted is None:
            raise NotImplementedError()
        return self.whitelisted


class MissingMorphoGateVault:
    """Fixture adapter that reproduces a malformed Morpho V2 gate response."""

    def __init__(self, address: str) -> None:
        self.address = address

    def is_whitelisted_deposit(self) -> bool:
        """Raise the typed ABI-read failure produced by an empty gate response."""

        raise MorphoGateAddressMissing(
            self.address,
            "receiveSharesGate",
            "latest",
            b"",
        )


def test_migrate_vault_deposit_permissions_updates_only_permission_field(tmp_path: Path) -> None:
    """Live permission reads correct stale cached classifications safely.

    1. Create EVM and synthetic cached metadata rows.
    2. Read live permission values through fixture adapters.
    3. Persist only the stale EVM permission with a backup.
    """

    # 1. Create EVM and synthetic cached metadata rows.
    migration = load_migration_module()
    stale_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    current_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    synthetic_spec = VaultSpec(9999, "0x0000000000000000000000000000000000000003")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    rows = {
        stale_spec: create_row(stale_spec, "whitelisted"),
        current_spec: create_row(current_spec, "permissionless"),
        synthetic_spec: create_row(synthetic_spec, "whitelisted"),
    }
    VaultDatabase(rows=rows).write(vault_db_path)

    # 2. Read live permission values through fixture adapters.
    def vault_factory(_web3: object, detection: ERC4262VaultDetection) -> FakeVault:
        return FakeVault(detection.address, whitelisted=False)

    result = migration.migrate_vault_deposit_permissions(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object()},
        vault_factory=vault_factory,
    )

    # 3. Persist only the stale EVM permission with a backup.
    migrated_db = VaultDatabase.read(vault_db_path)
    assert result.inspected_rows == len(rows)
    assert result.candidate_rows == len(rows) - 1
    assert len(result.updated_rows) == 1
    assert result.updated_rows[0].spec == stale_spec
    assert (tmp_path / "vault-metadata-db.pickle.bak-deposit-permission").exists()
    assert migrated_db.rows[stale_spec]["_deposit_permission"] == "permissionless"
    assert migrated_db.rows[current_spec]["_deposit_permission"] == "permissionless"
    assert migrated_db.rows[synthetic_spec]["_deposit_permission"] == "whitelisted"


def test_migrate_vault_deposit_permissions_preserves_explicit_value_when_read_is_unknown(tmp_path: Path) -> None:
    """An inconclusive adapter read does not overwrite an explicit policy.

    1. Create a cached explicit permission classification.
    2. Simulate an adapter read that resolves to unknown.
    3. Confirm the migration leaves the pickle and backup untouched.
    """

    # 1. Create a cached explicit permission classification.
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: create_row(spec, "whitelisted")}).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    # 2. Simulate an adapter read that resolves to unknown.
    def vault_factory(_web3: object, detection: ERC4262VaultDetection) -> FakeVault:
        return FakeVault(detection.address, whitelisted=None)

    result = migration.migrate_vault_deposit_permissions(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object()},
        vault_factory=vault_factory,
    )

    # 3. Confirm the migration leaves the pickle and backup untouched.
    assert not result.updated_rows
    assert result.unresolved_rows == 1
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-deposit-permission").exists()


def test_migration_continues_after_missing_morpho_gate_address(tmp_path: Path) -> None:
    """A malformed Morpho V2 gate response does not abort a metadata migration.

    1. Create a cached explicit permission classification.
    2. Reproduce the typed empty-gate failure through a fixture adapter.
    3. Confirm the migration preserves the row as unresolved without writing.
    """
    # 1. Create a cached explicit permission classification.
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: create_row(spec, "whitelisted")}).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    # 2. Reproduce the typed empty-gate failure through a fixture adapter.
    def vault_factory(_web3: object, detection: ERC4262VaultDetection) -> MissingMorphoGateVault:
        return MissingMorphoGateVault(detection.address)

    result = migration.migrate_vault_deposit_permissions(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object()},
        vault_factory=vault_factory,
    )

    # 3. Confirm the migration preserves the row as unresolved without writing.
    assert not result.updated_rows
    assert result.unresolved_rows == 1
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-deposit-permission").exists()


def test_permission_adapter_factory_skips_unsupported_cached_asseto_product(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale Asseto classification does not abort a metadata migration.

    1. Create a cached feature envelope accepted by the migration.
    2. Reproduce Asseto's explicit unsupported-product construction error.
    3. Confirm the adapter factory marks only this error unsupported.
    """
    # 1. Create a cached feature envelope accepted by the migration.
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    detection = create_detection(spec)

    # 2. Reproduce Asseto's explicit unsupported-product construction error.
    unsupported_product_error = "Unsupported Asseto product: chain=1, token=0x0000000000000000000000000000000000000001"

    def create_unsupported_product_vault(_web3: object, _address: str, _features: set, **_kwargs: object) -> None:
        raise RuntimeError(unsupported_product_error)

    monkeypatch.setattr(migration, "create_vault_instance", create_unsupported_product_vault)

    # 3. Confirm the adapter factory marks only this error unsupported.
    assert migration.create_vault_for_permission_read(object(), detection) is None
