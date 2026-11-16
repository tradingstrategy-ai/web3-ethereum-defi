"""Exercise PassivBot's curator attribution, native copy and metadata migration."""

from pathlib import Path

import pytest
from eth_typing import HexAddress
from PIL import Image

from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.curator import build_curator_metadata_json, get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.flag import HYPERCORE_VAULT_NOTE, VaultFlag, get_notes
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Exact reference deployment and numbered repair under test.
TARGET = VaultSpec(9999, "0x490af7d4a048a81db0f677517ed6373565b42349")
SCRIPT_NAME = "020-passivbot-canon-metadata.py"
FIELDS = ("_description", "_short_description", "_strategy_tags")
EXPECTED_TAGS = {
    StrategyTag.perpetual_futures,
    StrategyTag.algorithmic_trading,
    StrategyTag.high_frequency_high_win_rate,
    StrategyTag.martingale_like,
    StrategyTag.directional_trading,
    StrategyTag.mean_reversion,
    StrategyTag.market_making,
    StrategyTag.market_making_clob,
}


def test_passivbot_curator_profile() -> None:
    """Load the public project profile, article references and official logo.

    The shared feeder schema and frontend exporter must retain the GitHub
    homepage, visible reading list and distinction from protocol curation.

    :return: ``None`` after checking the profile and its published logo asset.
    """
    path = Path("eth_defi/data/feeds/curators/passivbot.yaml")
    feeder = load_feeder_metadata(path)
    metadata = build_curator_metadata_json(path, public_url="https://example.invalid")
    assert metadata["name"] == "PassivBot"
    assert metadata["website"] == "https://github.com/enarjord/passivbot"
    assert metadata["canonical_feeder_id"] is None
    assert metadata["protocol_curator"] is False
    assert metadata["short_description"] == "PassivBot is an open-source trading bot project for automated cryptocurrency futures strategies."
    assert "[PassivBot](https://github.com/enarjord/passivbot)" in metadata["long_description"]
    assert "[Passivbot Canon](https://tradingstrategy.ai/vaults/passivbot-canon)" in metadata["long_description"]
    for url in (
        "https://github.com/enarjord/passivbot/blob/master/docs/config.bot.md",
        "https://coinledger.io/tools/the-best-open-source-and-free-crypto-trading-bots",
        "https://ecoppen.github.io/futuresboard/passivbot/",
    ):
        assert any(line.startswith("- [") and url in line for line in metadata["long_description"].splitlines())
        assert any(link["url"] == url for link in feeder["other-links"])
    assert "historical community guidance" in metadata["long_description"]
    assert metadata["logos"]["generic"] == "https://example.invalid/curator-metadata/passivbot/generic.png"
    with Image.open("eth_defi/data/vaults/formatted_logos/passivbot/generic.png") as logo:
        assert logo.size == (256, 256)
        assert logo.mode == "RGBA"
        assert logo.getchannel("A").getbbox() is not None


def test_passivbot_curator_is_exact_deployment_only() -> None:
    """Attribute the reference vault without claiming independent software users.

    Reviewed address evidence must survive renaming and stale curator values.
    The same address on another chain and unrelated branded vaults stay unclaimed.

    :return: ``None`` after checking address precedence and attribution isolation.
    """
    for address in (TARGET.vault_address, TARGET.vault_address.upper()):
        assert identify_curator(TARGET.chain_id, "", "Renamed vault", address, "hyperliquid", declared_curator_slug="old-curator") == "passivbot"
    for chain in (1, 999):
        assert identify_curator(chain, "", "Passivbot Canon", TARGET.vault_address, "hyperliquid") is None
    for name in ("Passivbot Canon", "Independent PassivBot strategy", "PassivBot"):
        assert identify_curator(TARGET.chain_id, "", name, "0x0000000000000000000000000000000000000001", "hyperliquid") is None
    assert get_curator_name("passivbot") == "PassivBot"
    assert not is_protocol_curator("passivbot")


def test_passivbot_native_copy_and_tags() -> None:
    """Export Markdown strategy copy and every maintained classification.

    Both address casings must use the overlay while retaining native identity,
    fees, flags and notes. Resolver callers cannot mutate the maintained tags.

    :return: ``None`` after checking the exported fields and resolver isolation.
    """
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None
    assert metadata.description == normalise_vault_description(metadata.description)
    assert metadata.short_description == normalise_vault_short_description(metadata.short_description)
    for address in (TARGET.vault_address, TARGET.vault_address.upper()):
        spec, row = create_hyperliquid_vault_row(HexAddress(address), "Keep provider name", "Old template summary.", 1_000, None)
        assert spec == TARGET
        assert row["_description"] == metadata.description
        assert "\n\n#### How the strategy works\n\n" in row["_description"]
        assert "\n- [" in row["_description"]
        assert "10%" in row["_description"]
        assert row["_short_description"] == "Automated long-only crypto futures trading using PassivBot's reference strategy."
        assert row["_strategy_tags"] == EXPECTED_TAGS
        assert get_strategy_tags(address) == EXPECTED_TAGS
        assert row["Name"] == "Keep provider name"
        assert row["Link"] == metadata.link
        assert row["Perf fee"] == pytest.approx(0.10)
        assert row["_flags"] == {VaultFlag.perp_dex_trading_vault}
        assert "_notes" not in row
    assert get_notes(TARGET.vault_address, chain_id=TARGET.chain_id) == HYPERCORE_VAULT_NOTE
    get_strategy_tags(TARGET.vault_address).clear()
    assert get_strategy_tags(TARGET.vault_address) == EXPECTED_TAGS
    assert get_handwritten_vault_metadata(999, TARGET.vault_address) is None
    _, unrelated = create_hyperliquid_vault_row(HexAddress("0x0000000000000000000000000000000000000001"), "PassivBot copy", "Keep original copy.", 1_000, None)
    assert unrelated["_description"] is None
    assert unrelated["_short_description"] == "Keep original copy."
    assert unrelated["_strategy_tags"] == {StrategyTag.perpetual_futures}


@pytest.fixture
def passivbot_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare stale reference metadata and unrelated rows in isolated storage.

    Historical sentinel files expose accidental writes outside metadata. Only
    this task's migration is copied into the runner's test resource directory.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :return: Pipeline directory and single-migration resource directory.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    row.update({"_description": None, "_short_description": "Reference vault using Passivbot's template config.", "_strategy_tags": {StrategyTag.perpetual_futures}, "_notes": "Preserve existing note", "_manager_name": "Keep operator"})
    rows = {
        TARGET: row,
        VaultSpec(999, TARGET.vault_address): {"Protocol": "Other", "_description": "Keep other chain"},
        VaultSpec(9999, "0x0000000000000000000000000000000000000001"): {"Protocol": "Hyperliquid", "_description": "Keep unrelated vault"},
    }
    VaultDatabase(rows=rows, last_scanned_block={9999: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "vault-reader-state-1h.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_passivbot_migration_preview_apply_and_retry(passivbot_migration: tuple[Path, Path]) -> None:
    """Repair exactly three fields with byte-preserving preview and safe retries.

    Compare migrated data with fresh native export accessors. Keep all other
    rows, fields and scanner state, and verify the runner creates a valid backup.

    :param passivbot_migration: Isolated pipeline and migration resources.
    :return: ``None`` after checking preview, apply, backup and repeat execution.
    """
    data_dir, scripts_dir = passivbot_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    for field in FIELDS:
        assert after.rows[TARGET][field] == fresh[field]
        before.rows[TARGET][field] = fresh[field]
    assert after == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["inspected_rows"] == 1
    assert entry["other_data"]["updated_fields"] == len(FIELDS)
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "current", "wrong_protocol", "missing_detection", "missing_native_feature"])
def test_passivbot_migration_target_states(passivbot_migration: tuple[Path, Path], state: str) -> None:
    """Avoid absent/current rewrites and reject conflicting protocol evidence.

    Rejection must leave the database unchanged. No-op cases must report their
    scope without manufacturing a row or creating a backup.

    :param passivbot_migration: Isolated pipeline and migration resources.
    :param state: Target condition for no-op or rejection coverage.
    :return: ``None`` after checking persisted bytes and migration reporting.
    """
    data_dir, scripts_dir = passivbot_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "current":
        _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
        for field in FIELDS:
            database.rows[TARGET][field] = fresh[field]
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    elif state == "missing_detection":
        del database.rows[TARGET]["_detection_data"]
    else:
        database.rows[TARGET]["_detection_data"].features.clear()
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection", "missing_native_feature"}:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["updated_fields"] == 0
        assert entry["other_data"]["missing_specs"] == ([TARGET.as_string_id()] if state == "missing" else [])
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))


@pytest.mark.parametrize("state", ["stale", "current", "missing", "wrong_protocol"])
def test_passivbot_grid_tag_removal(passivbot_migration: tuple[Path, Path], state: str) -> None:
    """Remove the persisted grid tag without modifying unrelated metadata.

    Exercise the later migration independently of the initial copy refresh.
    Preview must preserve bytes, apply must preserve unrelated data, and a
    second apply must be skipped by the journal.

    :param passivbot_migration: Isolated pipeline and migration resources.
    :param state: Target condition before the tag-only migration.
    :return: ``None`` after checking the isolated migration result.
    """
    data_dir, scripts_dir = passivbot_migration
    (scripts_dir / SCRIPT_NAME).unlink()
    name = "073-passivbot-canon-strategy-tags.py"
    script = next(spec for spec in runner.discover_migrations() if spec.name == name)
    (scripts_dir / name).write_bytes(script.source)
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGET]["_strategy_tags"] = EXPECTED_TAGS | {StrategyTag.grid_trading} if state == "stale" else set(EXPECTED_TAGS)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    database.write(path)
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    if state == "wrong_protocol":
        with pytest.raises(ValueError, match="not classified as Hyperliquid"):
            runner.run_pending_migrations(dry_run=False, **options)
        assert path.read_bytes() == original[path.name]
        return

    runner.run_pending_migrations(dry_run=True, **options)
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    if state == "stale":
        database.rows[TARGET]["_strategy_tags"] = set(EXPECTED_TAGS)
    assert VaultDatabase.read(path) == database
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[name]
    assert entry["other_data"]["updated_fields"] == (1 if state == "stale" else 0)
    if state == "stale":
        assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    else:
        assert path.read_bytes() == original[path.name]
        assert not list(data_dir.glob("*.bak*"))
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied
