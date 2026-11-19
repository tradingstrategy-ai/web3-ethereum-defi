"""Check the scoped long-short portfolio tag refreshes for native Hyperliquid vaults."""

import datetime
from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

REVIEWED_UNLEVERED_VAULT_COUNT = 5


@pytest.mark.parametrize("state", ["stale", "current", "missing", "wrong_protocol", "missing_detection"])
@pytest.mark.parametrize(
    ("script_name", "address"),
    [
        ("076-hyperneutral-strategy-tags.py", "0x8bb033130be354eed1110ce7228d7095f001d9fe"),
        ("084-diversified-low-exposure-portfolios.py", "0x8bb033130be354eed1110ce7228d7095f001d9fe"),
        ("084-diversified-low-exposure-portfolios.py", "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),
        ("086-hyperliquid-unlevered-portfolios.py", "0x8bb033130be354eed1110ce7228d7095f001d9fe"),
        ("087-hyperliquid-daily-rebalance.py", "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),
        ("091-hyperliquid-algorithmic-portfolios.py", "0x8bb033130be354eed1110ce7228d7095f001d9fe"),
    ],
)
def test_hyperneutral_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, script_name: str, address: HexAddress) -> None:
    """Preview and apply only the reviewed vault's strategy field safely.

    Isolated storage verifies byte-preserving previews, unrelated row and field
    preservation, safe reruns and rejection of incorrect protocol evidence.

    :param tmp_path: Temporary migration storage.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :param state: Target metadata condition.
    :param script_name: Numbered refresh to run in isolation.
    :param address: Deployment whose persisted tags exercise the refresh.
    :return: ``None`` after checking persistence and reporting.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    target = VaultSpec(9999, address)
    expected = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.long_short_portfolio, StrategyTag.diversified_low_exposure_long_short_portfolio, StrategyTag.unlevered_portfolio, StrategyTag.daily_rebalance}
    timestamp = datetime.datetime(2026, 11, 14, tzinfo=datetime.UTC).replace(tzinfo=None)
    detection = ERC4262VaultDetection(chain=9999, address=target.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features={ERC4626Feature.hypercore_native}, updated_at=timestamp, deposit_count=1, redeem_count=1)
    row = {"Protocol": "Hyperliquid", "Name": "Preserve name", "_detection_data": detection, "_strategy_tags": expected if state == "current" else {StrategyTag.perpetual_futures}}
    database = VaultDatabase(rows={target: row, VaultSpec(999, target.vault_address): {"Name": "Preserve unrelated vault"}})
    if state == "missing":
        del database.rows[target]
    elif state == "wrong_protocol":
        row["Protocol"] = "Other"
    elif state == "missing_detection":
        del row["_detection_data"]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == script_name)
    (scripts_dir / script.name).write_bytes(script.source)
    original = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection"}:
        with pytest.raises(ValueError, match="not classified as Hyperliquid"):
            runner.run_pending_migrations(dry_run=False, **options)
        assert path.read_bytes() == original
        return
    runner.run_pending_migrations(dry_run=True, **options)
    assert path.read_bytes() == original
    assert {file.name for file in data_dir.iterdir()} == {path.name}
    runner.run_pending_migrations(dry_run=False, **options)
    if state == "stale":
        database.rows[target]["_strategy_tags"] = expected
    assert VaultDatabase.read(path) == database
    entry = runner.load_state(data_dir / "migration.json")[script.name]
    assert entry["other_data"]["updated_fields"] == (1 if state == "stale" else 0)
    if script_name.startswith("084"):
        missing = {VaultSpec(9999, addr).as_string_id() for addr in ("0x8bb033130be354eed1110ce7228d7095f001d9fe", "0x14ecb3f4932522b8d508fa6e237e2746a4999feb")}
        if state != "missing":
            missing.remove(target.as_string_id())
        assert set(entry["other_data"]["missing_specs"]) == missing
    elif script_name.startswith("086") and state == "missing":
        # An empty inventory lists the complete migration scope without creating any rows.
        missing = set(map(VaultSpec.parse_string, entry["other_data"]["missing_specs"]))
        assert len(missing) == REVIEWED_UNLEVERED_VAULT_COUNT
        assert all(spec.chain_id == target.chain_id and StrategyTag.unlevered_portfolio in get_strategy_tags(spec.vault_address) for spec in missing)
    applied = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert path.read_bytes() == applied
