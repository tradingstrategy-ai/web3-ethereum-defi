"""Tests for the cached Lagoon classification migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the Lagoon cache migration script as a test module.

    :return:
        Loaded migration module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-lagoon-classification.py"
    spec = importlib.util.spec_from_file_location("migrate_lagoon_classification", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create cached detection metadata for a vault fixture.

    :param spec:
        Vault identity.
    :param features:
        Cached scanner features.
    :return:
        Detection envelope matching metadata cache rows.
    """
    timestamp = datetime.datetime(2026, 7, 28, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_row(spec: VaultSpec, protocol: str, features: set[ERC4626Feature], protocol_slug: str) -> dict:
    """Create one minimal cached vault metadata row.

    :param spec:
        Vault identity.
    :param protocol:
        Cached protocol label.
    :param features:
        Cached feature flags.
    :param protocol_slug:
        Cached protocol URL slug.
    :return:
        Vault metadata row.
    """
    return {
        "Name": f"Vault {spec.vault_address[-4:]}",
        "Protocol": protocol,
        "Features": ", ".join(sorted(feature.name for feature in features)),
        "features": set(features),
        "protocol_slug": protocol_slug,
        "_detection_data": create_detection(spec, features),
    }


def test_migrate_lagoon_classifications_updates_only_automatic_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Re-probing updates recognised Lagoon cache rows and preserves others."""
    migration = load_migration_module()
    unknown_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    stale_lagoon_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    generic_erc7540_spec = VaultSpec(1, "0x0000000000000000000000000000000000000003")
    morpho_spec = VaultSpec(1, "0x0000000000000000000000000000000000000004")
    stale_features = {ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    lagoon_features = {ERC4626Feature.lagoon_like, *stale_features}

    vault_db = VaultDatabase(
        rows={
            unknown_spec: create_row(unknown_spec, "<unknown ERC-7540>", stale_features, "unknown"),
            stale_lagoon_spec: create_row(stale_lagoon_spec, "Lagoon Finance", stale_features, "unknown"),
            generic_erc7540_spec: create_row(generic_erc7540_spec, "<unknown ERC-7540>", stale_features, "unknown"),
            morpho_spec: create_row(morpho_spec, "Morpho", {ERC4626Feature.morpho_like}, "morpho"),
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    def detector(_web3: object, address: str) -> set[ERC4626Feature]:
        """Return current automatic features for the migration fixture."""
        if address in {unknown_spec.vault_address, stale_lagoon_spec.vault_address}:
            return set(lagoon_features)
        assert address == generic_erc7540_spec.vault_address
        return set(stale_features)

    result = migration.migrate_lagoon_classifications(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object()},
        detector=detector,
    )
    captured = capsys.readouterr()

    assert result.inspected_rows == len(vault_db.rows)
    assert result.candidate_rows == len({unknown_spec, stale_lagoon_spec, generic_erc7540_spec})
    assert result.recognised_lagoon_rows == len({unknown_spec, stale_lagoon_spec})
    assert {update.spec for update in result.updated_rows} == {unknown_spec, stale_lagoon_spec}
    assert "Old protocol" in captured.out
    assert unknown_spec.vault_address in captured.out
    assert (tmp_path / "vault-metadata-db.pickle.bak-lagoon-classification").exists()

    migrated_db = VaultDatabase.read(vault_db_path)
    for spec in (unknown_spec, stale_lagoon_spec):
        row = migrated_db.rows[spec]
        assert row["Protocol"] == "Lagoon Finance"
        assert row["protocol_slug"] == "lagoon-finance"
        assert row["features"] == lagoon_features
        assert row["_detection_data"].features == lagoon_features
    assert migrated_db.rows[generic_erc7540_spec]["Protocol"] == "<unknown ERC-7540>"
    assert migrated_db.rows[morpho_spec]["Protocol"] == "Morpho"


def test_migrate_lagoon_classifications_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dry runs show recognised Lagoon rows without changing the cache."""
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    stale_features = {ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    lagoon_features = {ERC4626Feature.lagoon_like, *stale_features}
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: create_row(spec, "<unknown ERC-7540>", stale_features, "unknown")}).write(vault_db_path)

    result = migration.migrate_lagoon_classifications(
        vault_db_path,
        dry_run=True,
        web3_by_chain={1: object()},
        detector=lambda _web3, _address: set(lagoon_features),
    )
    captured = capsys.readouterr()

    assert len(result.updated_rows) == 1
    assert spec.vault_address in captured.out
    unchanged_db = VaultDatabase.read(vault_db_path)
    assert unchanged_db.rows[spec]["Protocol"] == "<unknown ERC-7540>"
    assert unchanged_db.rows[spec]["protocol_slug"] == "unknown"
    assert not (tmp_path / "vault-metadata-db.pickle.bak-lagoon-classification").exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_parse_boolean_env_is_explicit(value: str | None, expected: object) -> None:
    """The write-mode switch accepts conventional explicit boolean values."""
    migration = load_migration_module()

    assert migration.parse_boolean_env(value, default=True) is expected


def test_parse_boolean_env_rejects_ambiguous_value() -> None:
    """An accidental dry-run value cannot turn a migration into a write."""
    migration = load_migration_module()

    with pytest.raises(ValueError, match="Expected a boolean"):
        migration.parse_boolean_env("definitely", default=True)


def test_migrate_lagoon_classifications_preserves_cached_non_lagoon_features(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A targeted reclassification cannot erase unrelated cached features."""
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    cached_features = {ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    detected_features = {ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like}
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: create_row(spec, "<unknown ERC-7540>", cached_features, "unknown")}).write(vault_db_path)

    migration.migrate_lagoon_classifications(
        vault_db_path,
        dry_run=False,
        web3_by_chain={1: object()},
        detector=lambda _web3, _address: set(detected_features),
    )
    capsys.readouterr()

    migrated_row = VaultDatabase.read(vault_db_path).rows[spec]
    assert migrated_row["features"] == cached_features | detected_features
    assert migrated_row["_detection_data"].features == cached_features | detected_features


def test_migrate_lagoon_classifications_aborts_before_writing_on_probe_error(tmp_path: Path) -> None:
    """A failed re-probe preserves the cache and does not create a backup."""
    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    features = {ERC4626Feature.erc_7540_like}
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: create_row(spec, "<unknown ERC-7540>", features, "unknown")}).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    def failed_detector(_web3: object, _address: str) -> set[ERC4626Feature]:
        """Simulate a re-probe failure."""
        error_message = "RPC timeout"
        raise TimeoutError(error_message)

    with pytest.raises(RuntimeError, match="No metadata cache was written"):
        migration.migrate_lagoon_classifications(
            vault_db_path,
            dry_run=False,
            web3_by_chain={1: object()},
            detector=failed_detector,
        )

    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-lagoon-classification").exists()
