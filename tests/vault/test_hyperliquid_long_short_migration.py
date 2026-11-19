"""Check the scoped Hyperliquid long-short portfolio tag refresh."""

from pathlib import Path

import pytest

from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner


def test_hyperliquid_long_short_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check the native export and refresh of a stale batch.

    A plain vault and a grid vault exercise the default tags and the retained
    strategy tags. An unrelated deployment with the same address and a
    partial inventory exercise the migration's scope.

    :param tmp_path: Isolated migration journal, scripts and metadata storage.
    :param monkeypatch: Remove legacy metadata path overrides.
    :return: ``None`` after checking preview and persisted metadata.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)

    edge, edge_row = create_hyperliquid_vault_row("0x060d01aa996003b3731a992462d7f0ba68bf3b04", "Edge & Hedge", None, 1_000, None)
    grids, grids_row = create_hyperliquid_vault_row("0x07fd993f0fa3a185f7207adccd29f7a87404689d", "L/S Grids", None, 1_000, None)
    database = VaultDatabase(rows={edge: edge_row, grids: grids_row, VaultSpec(999, edge.vault_address): {"Name": "Preserve unrelated row"}})
    base_tags = {StrategyTag.perpetual_futures}
    old_grid_tags = base_tags | {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.grid_trading, StrategyTag.high_frequency_high_win_rate}
    portfolio_tag = {StrategyTag.long_short_portfolio}
    assert edge_row["_strategy_tags"] == base_tags | portfolio_tag
    assert grids_row["_strategy_tags"] == old_grid_tags | portfolio_tag
    edge_row["_strategy_tags"] = base_tags
    grids_row["_strategy_tags"] = old_grid_tags

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    before_run = path.read_bytes()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "083-hyperliquid-long-short-portfolios.py")
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    runner.run_pending_migrations(dry_run=True, **options)
    assert path.read_bytes() == before_run
    assert not (data_dir / "migration.json").exists()

    runner.run_pending_migrations(dry_run=False, **options)
    edge_row["_strategy_tags"] = base_tags | portfolio_tag
    grids_row["_strategy_tags"] = old_grid_tags | portfolio_tag
    assert VaultDatabase.read(path) == database
    report = runner.load_state(data_dir / "migration.json")[script.name]["other_data"]
    assert (report["inspected_rows"], report["updated_fields"], len(report["missing_specs"])) == (2, 2, 7)
    assert all(target.chain_id == edge.chain_id and StrategyTag.long_short_portfolio in get_strategy_tags(target.vault_address) for target in map(VaultSpec.parse_string, report["missing_specs"]))

    before_run = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert path.read_bytes() == before_run
