"""Check the scoped Lighter public-pool strategy tag refresh."""

from pathlib import Path

import pytest

from eth_defi.lighter.constants import LIGHTER_ROBINHOOD
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner


def test_lighter_public_pool_strategy_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview and apply the Core classification to staggered scanner state.

    A stale Core row picks up the export's tags. A Robinhood row with the same
    numeric account index keeps its existing metadata.

    :param tmp_path: Isolated migration journal, scripts and metadata storage.
    :param monkeypatch: Fixture for removing metadata path overrides.
    :return: ``None`` after checking the preview, apply and rerun.
    """
    for variable in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(variable, raising=False)

    account_index = 281474976501540
    core, core_row = create_lighter_pool_row(account_index, "dima systematic long/short bot", None, 1_000, None)
    robinhood, robinhood_row = create_lighter_pool_row(account_index, "Other pool", None, 1_000, None, deployment=LIGHTER_ROBINHOOD)
    expected_tags = {
        StrategyTag.algorithmic_trading,
        StrategyTag.daily_rebalance,
        StrategyTag.delta_neutral,
        StrategyTag.leveraged_portfolio,
        StrategyTag.long_short_portfolio,
        StrategyTag.perpetual_futures,
    }
    assert core_row["_strategy_tags"] == expected_tags
    core_row["_strategy_tags"] = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.delta_neutral}
    database = VaultDatabase(rows={core: core_row, robinhood: robinhood_row})

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    original = path.read_bytes()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "092-lighter-public-pool-strategies.py")
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    runner.run_pending_migrations(dry_run=True, **options)
    assert path.read_bytes() == original
    assert not (data_dir / "migration.json").exists()

    runner.run_pending_migrations(dry_run=False, **options)
    core_row["_strategy_tags"] = expected_tags
    assert VaultDatabase.read(path) == database
    report = runner.load_state(data_dir / "migration.json")[script.name]["other_data"]
    assert report["inspected_rows"] == report["updated_fields"] == 1
    assert len(report["missing_specs"]) == 41

    migrated = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert path.read_bytes() == migrated
