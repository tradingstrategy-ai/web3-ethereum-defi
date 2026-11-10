"""Check the isolated Stratwise sentiment-tag refresh without provider reads."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed native deployment and its new persisted-field refresh.
TARGET = VaultSpec(9999, "0x0ff219ac20596b457558341bc410bc7a08a1394c")
SCRIPT_NAME = "056-stratwise-sentiment-tag.py"


@pytest.mark.parametrize("dry_run", [True, False])
def test_stratwise_sentiment_tag_refresh(tmp_path: Path, dry_run: bool) -> None:
    """Refresh only the target's tags and preserve its spot-only classification.

    Exercise preview, persistence and idempotence against an isolated metadata
    file, with a same-address EVM row outside the reviewed native chain.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether changes must remain in memory.
    :return: ``None`` after verifying the staged and persisted metadata.
    """
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", "Keep summary", 1_000, None)
    old_row = fresh | {"_strategy_tags": fresh["_strategy_tags"] - {StrategyTag.sentiment_trading}, "_notes": "Keep notes"}
    other_chain = VaultSpec(999, TARGET.vault_address)
    unrelated = {"_strategy_tags": {StrategyTag.lending}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={TARGET: old_row, other_chain: unrelated}).write(path)
    before = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    expected = old_row | {"_strategy_tags": fresh["_strategy_tags"]}
    assert context.metadata().rows == {TARGET: expected, other_chain: unrelated}
    assert StrategyTag.sentiment_trading in expected["_strategy_tags"]
    assert StrategyTag.perpetual_futures not in expected["_strategy_tags"]
    assert context.other_data["updated_fields"] == 1
    if dry_run:
        assert path.read_bytes() == before
    else:
        assert VaultDatabase.read(path).rows[TARGET] == expected
        applied = path.read_bytes()
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["updated_fields"] == 0
        assert path.read_bytes() == applied
