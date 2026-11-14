"""Check FuturAI Labs attribution, native tags and isolated migration scope."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import build_curator_metadata_json, identify_curator
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Two live mean-reversion vaults and their operator's closed breakout vault.
ADDRESSES = (
    "0xb65dd7c56afbf3b272ab5fc49be44b47dca18003",
    "0x53375ca9a649f337d83d0834fec0db97640a2a06",
    "0xbefed76a71682b1dc48cca5a10f397cbc492a216",
)


@pytest.mark.parametrize("address", ADDRESSES)
def test_futurai_attribution_and_native_tags(address: str) -> None:
    """Resolve renamed native vaults while preserving their distinct strategies.

    Exact mappings must work for mixed-case addresses and remain scoped to
    Hypercore. Native exports must carry the same reviewed tags.

    :param address: Reviewed native vault address.
    :return: ``None`` after checking attribution, scope and tags.
    """
    args = {"vault_token_symbol": "", "vault_name": "Renamed vault", "vault_address": address.upper(), "protocol_slug": "hyperliquid"}
    assert identify_curator(chain_id=9999, **args) == "futurai-labs"
    assert identify_curator(chain_id=999, **args) is None
    _, row = create_hyperliquid_vault_row(HexAddress(address.upper()), "Renamed vault", None, 1_000, None)
    expected = {StrategyTag.perpetual_futures, StrategyTag.directional_trading}
    if address == ADDRESSES[2]:
        expected.add(StrategyTag.trend_following)
    else:
        expected.update({StrategyTag.algorithmic_trading, StrategyTag.mean_reversion, StrategyTag.high_frequency_high_win_rate, StrategyTag.martingale_like})
    assert row["_strategy_tags"] == expected


def test_futurai_profile() -> None:
    """Load the public profile through the shared curator export schema.

    The public identity, descriptions and official social source must survive
    the same loading path used by production exports.

    :return: ``None`` after checking exported identity and descriptions.
    """
    path = Path("eth_defi/data/feeds/curators/futurai-labs.yaml")
    profile = build_curator_metadata_json(path)
    assert profile["name"] == "FuturAI Labs"
    assert profile["twitter"] == "https://x.com/FuturAILabs"
    assert profile["short_description"]
    assert profile["long_description"]


@pytest.mark.parametrize("script_name", ["051-futurai-labs-strategy-tags.py", "052-futurai-high-win-rate.py"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_futurai_tag_migration(tmp_path: Path, script_name: str, *, dry_run: bool) -> None:
    """Refresh all reviewed rows without altering unrelated metadata or chains.

    Exercise the real context, byte-preserving previews and idempotent applies
    against an isolated database. Missing targets must be reported.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether persistence is prohibited.
    :param script_name: Initial tag migration or subsequent high-win-rate refresh.
    :return: ``None`` after checking scope, counts and persistence.
    """
    rows = {}
    for address in ADDRESSES[:2]:
        spec, row = create_hyperliquid_vault_row(HexAddress(address), "Keep name", "Keep description", 1_000, None)
        rows[spec] = row | {"_strategy_tags": {StrategyTag.perpetual_futures}}
    unrelated = VaultSpec(999, ADDRESSES[0])
    rows[unrelated] = {"Name": "Keep unrelated"}
    breaking = VaultSpec(9999, ADDRESSES[2])
    if script_name.startswith("052"):
        rows[breaking] = {"Name": "Keep Breaking", "_strategy_tags": {StrategyTag.perpetual_futures}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(item for item in runner.discover_migrations() if item.name == script_name)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    assert context.other_data["changed_rows"] == len(ADDRESSES[:2])
    assert context.other_data["missing_specs"] == ([] if script_name.startswith("052") else [breaking.as_string_id()])
    if breaking in rows:
        assert context.metadata().rows[breaking] == rows[breaking]
    assert context.metadata().rows[unrelated] == rows[unrelated]
    for spec, original_row in rows.items():
        if spec in (unrelated, breaking):
            continue
        actual = context.metadata().rows[spec]
        assert actual | {"_strategy_tags": original_row["_strategy_tags"]} == original_row
        assert StrategyTag.mean_reversion in actual["_strategy_tags"]
        assert StrategyTag.high_frequency_high_win_rate in actual["_strategy_tags"]
    if dry_run:
        assert path.read_bytes() == original
    else:
        applied = path.read_bytes()
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0
        assert path.read_bytes() == applied


def test_futurai_migration_rejects_wrong_protocol(tmp_path: Path) -> None:
    """Reject conflicting target detection before persisting any change.

    A same-address row without native Hyperliquid evidence must never acquire
    these tags. Exercise the guard against an isolated file-backed database.

    :param tmp_path: Isolated pipeline directory.
    :return: ``None`` after checking rejection and unchanged bytes.
    """
    spec = VaultSpec(9999, ADDRESSES[0])
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: {"Name": "Wrong protocol"}}).write(path)
    original = path.read_bytes()
    migration = next(item for item in runner.discover_migrations() if item.name == "051-futurai-labs-strategy-tags.py")
    context = runner.MigrationContext(tmp_path, False, script_name=migration.name)
    with pytest.raises(ValueError, match="not a native Hyperliquid vault"):
        runner._execute(migration, context)
    assert path.read_bytes() == original
