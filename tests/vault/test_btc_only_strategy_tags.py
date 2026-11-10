"""Check Bitcoin-only presentation, native exports and persisted tag refreshes."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import STRATEGY_TAG_METADATA, StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Bunny and BitRocket, confirmed through live Hyperliquid vaultDetails.
ADDRESSES = (
    "0x5ce0ce4387679de0335a3e8a1c9a7abb4f150a71",
    "0xa415c4ded17cac76a1d81a54d9f4aeb48ffd4dd0",
)
SCRIPT_NAME = "054-btc-only-strategy-tags.py"


@pytest.mark.parametrize("address", ADDRESSES)
def test_btc_only_export(address: str) -> None:
    """Export Bitcoin-only exposure alongside the existing trading classifications.

    Check the persisted identifier, public copy and normalised native resolver
    together so the new category survives the same path as existing tags.

    :param address: Reviewed native Hyperliquid vault address.
    :return: ``None`` after checking metadata and exported classifications.
    """
    assert StrategyTag("btc_only") is StrategyTag.btc_only
    assert STRATEGY_TAG_METADATA[StrategyTag.btc_only] == {"label": "Bitcoin only", "description": "The strategy trades only Bitcoin"}
    expected = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate, StrategyTag.btc_only}
    _, row = create_hyperliquid_vault_row(HexAddress(address.upper()), "Renamed vault", None, 1_000, None)
    assert row["_strategy_tags"] == expected
    assert get_strategy_tags(address.upper()) == expected
    get_strategy_tags(address).clear()
    assert get_strategy_tags(address) == expected
    assert get_strategy_tags("0x0000000000000000000000000000000000000001") == {StrategyTag.perpetual_futures}


@pytest.mark.parametrize("dry_run", [True, False])
def test_btc_only_migration(tmp_path: Path, *, dry_run: bool) -> None:
    """Refresh only the two reviewed native rows and preserve previews and retries.

    Exercise isolated file-backed metadata, including a same-address row on
    another chain and an unrelated native vault, without touching live data.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether the migration must leave database bytes unchanged.
    :return: ``None`` after checking scope, persistence and idempotence.
    """
    rows = {}
    old_tags = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate}
    for address in ADDRESSES:
        spec, row = create_hyperliquid_vault_row(HexAddress(address), "Keep name", "Keep description", 1_000, None)
        rows[spec] = row | {"_strategy_tags": old_tags.copy()}
    rows[VaultSpec(999, ADDRESSES[0])] = {"Name": "Keep other chain"}
    rows[VaultSpec(9999, "0x0000000000000000000000000000000000000001")] = {"Name": "Keep unrelated native vault"}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=SCRIPT_NAME)
    runner._execute(migration, context)
    assert context.other_data["changed_rows"] == len(ADDRESSES)
    assert context.other_data["updated_fields"] == len(ADDRESSES)
    assert context.other_data["missing_specs"] == []
    expected_rows = {spec: row | {"_strategy_tags": old_tags | {StrategyTag.btc_only}} if spec in {VaultSpec(9999, address) for address in ADDRESSES} else row for spec, row in rows.items()}
    assert context.metadata().rows == expected_rows
    if dry_run:
        assert path.read_bytes() == original
    else:
        assert VaultDatabase.read(path).rows == expected_rows
        applied = path.read_bytes()
        repeated = runner.MigrationContext(tmp_path, False, script_name=SCRIPT_NAME)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0
        assert path.read_bytes() == applied


@pytest.mark.parametrize("present", [False, True])
def test_btc_only_migration_missing_or_conflicting_targets(tmp_path: Path, *, present: bool) -> None:
    """Report absent targets and reject conflicting detection without persisting.

    A matching identifier alone must never classify an unsupported protocol.
    Missing targets must remain absent rather than creating synthetic rows.

    :param tmp_path: Isolated pipeline directory.
    :param present: Whether to add a conflicting target instead of leaving it absent.
    :return: ``None`` after checking rejection or missing-target counts.
    """
    rows = {}
    if present:
        spec, row = create_hyperliquid_vault_row(HexAddress(ADDRESSES[0]), "Wrong protocol", None, 1_000, None)
        rows[spec] = row | {"Protocol": "Other"}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, False, script_name=SCRIPT_NAME)
    if present:
        with pytest.raises(ValueError, match="not a native Hyperliquid vault"):
            runner._execute(migration, context)
    else:
        runner._execute(migration, context)
        assert context.other_data["changed_rows"] == 0
        assert context.other_data["missing_specs"] == [VaultSpec(9999, address).as_string_id() for address in ADDRESSES]
    assert path.read_bytes() == original


def test_btc_only_migration_batch_order(tmp_path: Path) -> None:
    """Refresh Bunny in 053 and only BitRocket in the following migration.

    Both scripts resolve the current mappings. Exercise their production order
    so the reported per-script counts reflect the overlapping Bunny target.

    :param tmp_path: Isolated pipeline and migration resources.
    :return: ``None`` after verifying ordered reports and final tags.
    """
    rows = {}
    for address in ADDRESSES:
        spec, row = create_hyperliquid_vault_row(HexAddress(address), "Keep name", None, 1_000, None)
        rows[spec] = row | {"_strategy_tags": {StrategyTag.perpetual_futures}}
    VaultDatabase(rows=rows).write(tmp_path / "vault-metadata-db.pickle")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    names = {"053-bunny-strategy-tags.py", SCRIPT_NAME}
    for script in runner.discover_migrations():
        if script.name in names:
            (scripts_dir / script.name).write_bytes(script.source)
    runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=scripts_dir, dry_run=False, provenance={"in_docker": False})
    journal = runner.load_state(tmp_path / "migration.json")
    for name in names:
        assert journal[name]["other_data"]["changed_rows"] == 1
        assert journal[name]["other_data"]["updated_fields"] == 1
    assert all(StrategyTag.btc_only in row["_strategy_tags"] for row in VaultDatabase.read(tmp_path / "vault-metadata-db.pickle").rows.values())
