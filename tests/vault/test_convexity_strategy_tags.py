"""Check +convexity's native export and isolated persisted tag refresh."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed +convexity native deployment and its field-only migration.
TARGET = VaultSpec(9999, "0x5661a070eb13c7c55ac3210b2447d4bea426cbf5")
SCRIPT_NAME = "080-convexity-bitcoin-only.py"
EXPECTED_TAGS = {StrategyTag.btc_only, StrategyTag.positive_convexity, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.perpetual_futures}


def test_convexity_native_export() -> None:
    """Export the assigned strategy profile alongside the native futures default.

    Resolve by address even when a vault is renamed, preserving the default
    classification for unrelated native vaults.

    :return: ``None`` after checking the resolver and exported metadata.
    """
    _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address.upper()), "Renamed vault", None, 1_000, None)
    assert row["_strategy_tags"] == EXPECTED_TAGS
    assert get_strategy_tags(TARGET.vault_address.upper()) == EXPECTED_TAGS
    assert get_strategy_tags("0x0000000000000000000000000000000000000001") == {StrategyTag.perpetual_futures}


@pytest.mark.parametrize("dry_run", [True, False])
def test_convexity_tag_refresh(tmp_path: Path, dry_run: bool) -> None:
    """Refresh only the target's tags and preserve preview and retry semantics.

    Exercise isolated file-backed metadata with another native vault and a
    same-address row on a different chain, keeping all other fields intact.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether changes must remain in memory.
    :return: ``None`` after checking scope, persistence and idempotence.
    """
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", "Keep description", 1_000, None)
    old_row = fresh | {"_strategy_tags": EXPECTED_TAGS - {StrategyTag.btc_only}, "_notes": "Keep notes"}
    other_chain = VaultSpec(999, TARGET.vault_address)
    other_vault = VaultSpec(9999, "0x0000000000000000000000000000000000000001")
    unrelated = {"_strategy_tags": {StrategyTag.lending}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={TARGET: old_row, other_chain: unrelated, other_vault: unrelated}).write(path)
    before = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=SCRIPT_NAME)
    runner._execute(migration, context)
    expected = {TARGET: old_row | {"_strategy_tags": EXPECTED_TAGS}, other_chain: unrelated, other_vault: unrelated}
    assert context.metadata().rows == expected
    assert context.other_data["changed_rows"] == 1
    assert context.other_data["updated_fields"] == 1
    assert context.other_data["missing_specs"] == []
    if dry_run:
        assert path.read_bytes() == before
    else:
        assert VaultDatabase.read(path).rows == expected
        applied = path.read_bytes()
        repeated = runner.MigrationContext(tmp_path, False, script_name=SCRIPT_NAME)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0
        assert path.read_bytes() == applied


@pytest.mark.parametrize("present", [False, True])
def test_convexity_missing_or_conflicting_target(tmp_path: Path, present: bool) -> None:
    """Report absent targets and reject conflicting protocol evidence without writes.

    A matching address alone must not classify another protocol or create a
    new synthetic row in the persisted metadata.

    :param tmp_path: Isolated pipeline directory.
    :param present: Whether to supply conflicting protocol evidence.
    :return: ``None`` after checking validation and unchanged database bytes.
    """
    rows = {}
    if present:
        _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Wrong protocol", None, 1_000, None)
        rows[TARGET] = row | {"Protocol": "Other"}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    before = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, False, script_name=SCRIPT_NAME)
    if present:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner._execute(migration, context)
    else:
        runner._execute(migration, context)
        assert context.other_data["missing_specs"] == [TARGET.as_string_id()]
        assert context.other_data["changed_rows"] == 0
    assert path.read_bytes() == before
