"""Check Systemic Strategies' maintained vault copy and isolated migration."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Exact deployment and later copy repair; tag migration 041 remains separate.
TARGET = VaultSpec(9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d")
SCRIPT_NAME = "042-systemic-strategies-description.py"
FIELDS = ("_description", "_short_description")


@pytest.mark.parametrize("address", [TARGET.vault_address, TARGET.vault_address.upper()])
def test_systemic_native_copy(address: str) -> None:
    """Use handwritten copy for native rows without changing fees or tags.

    Provider marketing must not displace the maintained copy after a rescan.
    Both address casings resolve to the same reviewed deployment.

    :param address: Lowercase or uppercase native vault address.
    :return: ``None`` after checking copy and existing protocol fields.
    """
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, address)
    assert metadata is not None
    spec, row = create_hyperliquid_vault_row(HexAddress(address), "Keep provider name", "Join the eternal money printer", 1_000, None)
    assert spec == TARGET
    assert row["_description"] == normalise_vault_description(metadata.description)
    assert row["_short_description"] == normalise_vault_short_description(metadata.short_description)
    assert "\n\n#### How the strategy works\n\n" in row["_description"]
    assert "\n" not in row["_short_description"]
    assert "money printer" not in row["_short_description"]
    assert row["Name"] == "Keep provider name"
    assert row["Perf fee"] == pytest.approx(0.10)
    assert StrategyTag.high_frequency_high_win_rate in row["_strategy_tags"]
    assert metadata.link == "https://tradingstrategy.ai/vaults/systemic-strategies-l-s-grids"
    assert get_handwritten_vault_metadata(999, address) is None
    assert get_handwritten_vault_metadata(9999, "0xd6e56265890b76413d1d527eb9b75e334c0c5b42") is None


@pytest.mark.parametrize("dry_run", [True, False])
def test_systemic_copy_migration(tmp_path: Path, dry_run: bool) -> None:
    """Refresh two text fields while preserving preview bytes and all other data.

    Exercise the real migration context with an unrelated same-address EVM
    deployment and verify repeat application does not rewrite metadata.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether the repair must stay in memory.
    :return: ``None`` after checking scope, persistence and idempotence.
    """
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", "Old summary", 1_000, None)
    old_row = fresh | {"_description": None, "_short_description": "Old summary", "_notes": "Keep note"}
    other_chain = VaultSpec(999, TARGET.vault_address)
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={TARGET: old_row, other_chain: {"_description": "Keep other chain"}}).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    expected = old_row | {field: fresh[field] for field in FIELDS}
    assert context.metadata().rows[TARGET] == expected
    assert context.metadata().rows[other_chain] == {"_description": "Keep other chain"}
    assert context.other_data["updated_fields"] == 2
    if dry_run:
        assert path.read_bytes() == original
    else:
        assert VaultDatabase.read(path).rows[TARGET] == expected
        applied = path.read_bytes()
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["updated_fields"] == 0
        assert path.read_bytes() == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol", "missing_detection"])
def test_systemic_copy_migration_guards(tmp_path: Path, state: str) -> None:
    """Report missing rows and reject conflicting identity before writing.

    An absent vault must not be created and unrelated metadata must survive
    failed validation unchanged.

    :param tmp_path: Isolated pipeline directory.
    :param state: Missing or incorrectly classified target condition.
    :return: ``None`` after checking the report or error and unchanged bytes.
    """
    _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    if state == "wrong_protocol":
        row["Protocol"] = "Other"
    elif state == "missing_detection":
        del row["_detection_data"]
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={} if state == "missing" else {TARGET: row}).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, False, script_name=migration.name)
    if state == "missing":
        runner._execute(migration, context)
        assert context.other_data["missing_specs"] == [TARGET.as_string_id()]
        assert context.other_data["updated_fields"] == 0
    else:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner._execute(migration, context)
    assert path.read_bytes() == original
