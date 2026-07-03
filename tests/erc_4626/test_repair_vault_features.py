"""Tests for vault feature repair script."""

import datetime
import importlib.util
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_INSPECTED_ROWS = 5
EXPECTED_REPAIRED_ROWS = 3


def load_repair_module():
    """Load the feature repair script as a test module.

    :return:
        Loaded repair script module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "repair-vault-features.py"
    spec = importlib.util.spec_from_file_location("repair_vault_features", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(
    spec: VaultSpec,
    features: set[ERC4626Feature] | None = None,
) -> ERC4262VaultDetection:
    """Create a minimal vault detection object.

    :param spec:
        Vault spec to use.

    :param features:
        Detection feature set.

    :return:
        Detection object suitable for ``VaultDatabase`` rows.
    """
    timestamp = datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features or set()),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_row(
    spec: VaultSpec,
    row_features: set[ERC4626Feature] | set[str] | None,
    detection_features: set[ERC4626Feature] | None,
) -> dict:
    """Create a minimal vault metadata row.

    :param spec:
        Vault spec to use.

    :param row_features:
        Top-level row features. Older pickles may store these as strings.

    :param detection_features:
        Detection features.

    :return:
        Vault metadata row.
    """
    row = {
        "Name": "Feature repair vault",
        "Protocol": "USDai",
        "_detection_data": create_detection(spec, detection_features),
    }
    if row_features is not None:
        row["features"] = set(row_features)
    return row


def test_repair_vault_features_copies_detection_features_to_row(tmp_path: Path) -> None:
    """Feature repair copies authoritative detection features to the row."""
    repair = load_repair_module()

    specs = {
        "detection_only": VaultSpec(42161, "0x0000000000000000000000000000000000000001"),
        "row_only": VaultSpec(42161, "0x0000000000000000000000000000000000000002"),
        "stale_row": VaultSpec(42161, "0x0000000000000000000000000000000000000003"),
        "string_row": VaultSpec(42161, "0x0000000000000000000000000000000000000004"),
        "complete": VaultSpec(42161, "0x0000000000000000000000000000000000000005"),
    }
    usdai_features = {ERC4626Feature.usdai_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    nashpoint_features = {ERC4626Feature.nashpoint_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}

    vault_db = VaultDatabase(
        rows={
            specs["detection_only"]: create_row(specs["detection_only"], None, usdai_features),
            specs["row_only"]: create_row(specs["row_only"], nashpoint_features, set()),
            specs["stale_row"]: create_row(specs["stale_row"], {"nashpoint_like", "erc_7540_like", "erc_7575_like"}, usdai_features),
            specs["string_row"]: create_row(specs["string_row"], {"usdai_like", "erc_7540_like", "erc_7575_like"}, usdai_features),
            specs["complete"]: create_row(specs["complete"], usdai_features, usdai_features),
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    result = repair.repair_vault_features(vault_db_path, dry_run=False)

    assert result.inspected_rows == EXPECTED_INSPECTED_ROWS
    assert result.repaired_rows == EXPECTED_REPAIRED_ROWS
    assert result.missing_detection_rows == 0

    repaired_db = VaultDatabase.read(vault_db_path)
    detection_only_row = repaired_db.rows[specs["detection_only"]]
    assert detection_only_row["features"] == usdai_features
    assert detection_only_row["_detection_data"].features == usdai_features

    row_only_row = repaired_db.rows[specs["row_only"]]
    assert row_only_row["features"] == nashpoint_features
    assert row_only_row["_detection_data"].features == set()

    stale_row = repaired_db.rows[specs["stale_row"]]
    assert stale_row["features"] == usdai_features
    assert stale_row["_detection_data"].features == usdai_features

    string_row = repaired_db.rows[specs["string_row"]]
    assert string_row["features"] == usdai_features
    assert all(isinstance(feature, ERC4626Feature) for feature in string_row["features"])


def test_repair_vault_features_dry_run_does_not_write(tmp_path: Path) -> None:
    """Dry-run repair reports changes without writing the pickle."""
    repair = load_repair_module()

    spec = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    features = {ERC4626Feature.usdai_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    vault_db = VaultDatabase(rows={spec: create_row(spec, None, features)})
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    result = repair.repair_vault_features(vault_db_path, dry_run=True)

    assert result.repaired_rows == 1
    unchanged_db = VaultDatabase.read(vault_db_path)
    assert "features" not in unchanged_db.rows[spec]


def test_normalise_features_accepts_string_values() -> None:
    """Feature normalisation accepts legacy string encodings."""
    repair = load_repair_module()

    assert repair._normalise_features("usdai_like") == {ERC4626Feature.usdai_like}
    assert repair._normalise_features({"erc_7540_like", "erc_7575_like"}) == {ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}


def test_create_backup_path_does_not_overwrite_existing_backups(tmp_path: Path) -> None:
    """Backup path selection keeps existing production backups."""
    repair = load_repair_module()

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    first_backup_path = tmp_path / "vault-metadata-db.pickle.bak-feature-repair"
    second_backup_path = tmp_path / "vault-metadata-db.pickle.bak-feature-repair.1"

    vault_db_path.touch()
    first_backup_path.touch()

    assert repair.create_backup_path(vault_db_path) == second_backup_path
