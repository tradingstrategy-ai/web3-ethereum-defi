"""Exercise the Neutral Trade Autopilot refresh through the numbered runner on isolated files."""

from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Migration under test.
SCRIPT_NAME = "011-neutral-trade-autopilot-metadata.py"

#: Ethereum and Monad deployments frozen in the migration on 2026-10-29.
TARGETS = (
    VaultSpec(1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
)


@pytest.fixture
def autopilot_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Build stale Autopilot metadata and isolate the new script from earlier repairs.

    Include both reviewed deployments, an unrelated deployment with the same
    address on another chain, and sentinel historical data.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for pipeline path overrides.
    :return: Pipeline directory and single-script resource directory.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {
        spec: {
            "Protocol": "Accountable",
            "Name": "Neutral Trade Autopilot",
            "Link": "https://www.neutral.trade/strategies/neutral-autopilot",
            "_description": "Old copy",
            "_short_description": "Old summary",
            "_notes": None,
            "_strategy_tags": None,
            "_flags": {VaultFlag.review_needed},
            "_manager_name": "Keep manager",
            "Perf fee": 0.03,
        }
        for spec in TARGETS
    }
    rows[VaultSpec(8453, TARGETS[0].vault_address)] = {"Protocol": "Other", "_notes": "Keep unrelated notes"}
    VaultDatabase(rows=rows, last_scanned_block={1: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_autopilot_migration_preview_apply_and_retry(autopilot_migration: tuple[Path, Path]) -> None:
    """Refresh both feeder vaults without changing unrelated state or preview bytes.

    1. Preview leaves every file byte-identical.
    2. Apply persists the same values the Accountable adapter and notes return.
    3. Names, flags, fees, manager names and historical files are preserved.
    4. The backup holds the original database and a repeat run is skipped.

    :param autopilot_migration: Isolated metadata and migration resources.
    :return: ``None`` after preview, apply and repeat validation.
    """
    data_dir, scripts_dir = autopilot_migration
    original = {path.name: path.read_bytes() for path in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    # 1. Preview leaves every file byte-identical.
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == original

    # 2. Apply persists the same values the Accountable adapter and notes return.
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    database = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    assert database.last_scanned_block == {1: 12345}
    for spec, row in database.rows.items():
        if row["Protocol"] != "Accountable":
            assert row == {"Protocol": "Other", "_notes": "Keep unrelated notes"}
            continue
        vault = AccountableVault(Web3(), VaultSpec(spec.chain_id, Web3.to_checksum_address(spec.vault_address)))
        assert row["_description"] == vault.description
        assert row["_short_description"] == vault.short_description
        assert row["Link"] == vault.get_link()
        assert row["Link"].startswith("https://yield.accountable.capital/vaults/")
        assert row["_notes"] == get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="Accountable")
        assert row["_strategy_tags"] == vault.get_strategy_tags()

        # 3. Names, flags, fees and manager names are preserved.
        assert row["Name"] == "Neutral Trade Autopilot"
        assert row["_flags"] == {VaultFlag.review_needed}
        assert row["_manager_name"] == "Keep manager"
        assert row["Perf fee"] == pytest.approx(0.03)
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != "vault-metadata-db.pickle")

    # 4. The backup holds the original database and a repeat run is skipped.
    entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
    assert entry["other_data"]["inspected_rows"] == 2  # noqa: PLR2004 - Frozen reviewed deployment count.
    assert entry["other_data"]["updated_fields"] == 10  # noqa: PLR2004 - Five refreshed fields on each deployment.
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original["vault-metadata-db.pickle"]
    applied = {path.name: path.read_bytes() for path in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == applied


@pytest.mark.parametrize("missing", [False, True])
def test_autopilot_migration_validates_targets(autopilot_migration: tuple[Path, Path], *, missing: bool) -> None:
    """Report absent deployments and reject unexpected classifications before writing.

    A missing row must not be manufactured. A conflicting row must stop the
    repair before the other target receives a partial update.

    :param autopilot_migration: Isolated metadata and migration resources.
    :param missing: Remove the Monad target instead of misclassifying it.
    :return: ``None`` after checking missing-row or failure handling.
    """
    data_dir, scripts_dir = autopilot_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    target = TARGETS[1]
    if missing:
        del database.rows[target]
    else:
        database.rows[target]["Protocol"] = "Unexpected"
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if missing:
        runner.run_pending_migrations(**options)
        assert target not in VaultDatabase.read(path).rows
        entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
        assert entry["other_data"]["missing_specs"] == [target.as_string_id()]
        assert entry["other_data"]["inspected_rows"] == 1
    else:
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(**options)
        assert path.read_bytes() == before
        assert not list(data_dir.glob("*.bak*"))


#: Markdown backfill migration under test.
MARKDOWN_SCRIPT_NAME = "015-neutral-trade-autopilot-markdown.py"


@pytest.fixture
def markdown_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Build rows holding the single-line copy persisted by migration 011.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for pipeline path overrides.
    :return: Pipeline directory and single-script resource directory.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {
        spec: {
            "Protocol": "Accountable",
            "_description": "This vault, built with Accountable, accepts USDC. Single dense paragraph.",
            "_short_description": "Keep summary.",
            "_notes": "Keep notes.",
            "Link": "https://example.com/keep",
        }
        for spec in TARGETS
    }
    VaultDatabase(rows=rows, last_scanned_block={1: 12345}).write(data_dir / "vault-metadata-db.pickle")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == MARKDOWN_SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_markdown_migration_preview_apply_and_retry(markdown_migration: tuple[Path, Path]) -> None:
    """Replace only the description with structured Markdown.

    1. Preview leaves the database byte-identical.
    2. Apply writes the adapter's Markdown description with paragraphs, subheadings, bold text and a link to the other network's vault.
    3. Summaries, notes and links are unchanged and a repeat run is skipped.

    :param markdown_migration: Isolated metadata and migration resources.
    :return: ``None`` after preview, apply and repeat validation.
    """
    data_dir, scripts_dir = markdown_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    # 1. Preview leaves the database byte-identical.
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert path.read_bytes() == original

    # 2. Apply writes structured Markdown matching the adapter.
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    database = VaultDatabase.read(path)
    other_vault_links = {1: "0x5ee57E42DF67e5707F0CAE1a18DfeaDB4F0Df86c", 143: "0x56B935Fe5183cC0DE489233d032F9A4B8ec2f9Ff"}
    for spec in TARGETS:
        row = database.rows[spec]
        vault = AccountableVault(Web3(), VaultSpec(spec.chain_id, Web3.to_checksum_address(spec.vault_address)))
        assert row["_description"] == vault.description
        paragraphs = row["_description"].split("\n\n")
        assert "#### Shared capital and custody" in paragraphs
        assert all(paragraph.count(". ") <= 2 for paragraph in paragraphs)  # noqa: PLR2004 - At most two sentence boundaries in a paragraph of up to three sentences.
        assert "**no management fee and a 3% performance fee**" in row["_description"]
        assert other_vault_links[spec.chain_id] in row["_description"]

        # 3. Summaries, notes and links are unchanged.
        assert row["_short_description"] == "Keep summary."
        assert row["_notes"] == "Keep notes."
        assert row["Link"] == "https://example.com/keep"
    entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
    assert entry["other_data"]["inspected_rows"] == 2  # noqa: PLR2004 - Frozen reviewed deployment count.
    assert entry["other_data"]["updated_fields"] == 2  # noqa: PLR2004 - One description per deployment.
    applied = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert path.read_bytes() == applied


def test_markdown_migration_rejects_unexpected_protocol(markdown_migration: tuple[Path, Path]) -> None:
    """Abort before writing when a target is no longer an Accountable vault.

    :param markdown_migration: Isolated metadata and migration resources.
    :return: ``None`` after checking that the database is unchanged.
    """
    data_dir, scripts_dir = markdown_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGETS[1]]["Protocol"] = "Unexpected"
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unexpected protocol"):
        runner.run_pending_migrations(data_dir=data_dir, scripts_dir=scripts_dir, dry_run=False, provenance={"in_docker": False})
    assert path.read_bytes() == before
