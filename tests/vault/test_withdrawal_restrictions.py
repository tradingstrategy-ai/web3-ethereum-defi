"""Verify descriptive exit warnings and their isolated persisted refresh."""

import runpy
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_TEXAS_HEDGE_ADDRESS, D2Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import BAD_FLAGS, SUBVAULT, VaultFlag, get_notes, get_vault_special_flags
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Explicit three-deployment refresh, plus all D2 rows regardless of address inventory.
SCRIPT_NAME = "024-withdrawal-restrictions.py"
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "migration" / "scripts" / SCRIPT_NAME
TARGETS = runpy.run_path(str(SCRIPT_PATH))["TARGETS"]
D2_TARGETS = (
    VaultSpec(42161, "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"),
    VaultSpec(999, D2_TEXAS_HEDGE_ADDRESS),
    VaultSpec(42161, "0x64167cd42859f64cff2aa4b63c3175ccef9659dd"),
    VaultSpec(999, "0x000000000000000000000000000000000000d2d2"),
)

#: Liquidity-constrained pools must keep their separate recovery-risk warning.
FORTY_ACRES_TARGETS = (
    VaultSpec(8453, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"),
    VaultSpec(10, "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957"),
    VaultSpec(43114, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7"),
    VaultSpec(43114, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd"),
)


def test_withdrawal_warning_is_descriptive_and_preserves_existing_flags() -> None:
    """Keep reviewed vaults visible while retaining their existing warnings.

    The shared warning describes exit mechanics and must not blacklist vaults
    or replace product-specific review and withdrawal-risk classifications.

    :return: ``None`` after checking exact flags and untouched unrelated pools.
    """
    assert VaultFlag.withdrawal_restrictions not in BAD_FLAGS
    for spec, protocol in TARGETS:
        flags = get_vault_special_flags(spec.vault_address, protocol)
        assert VaultFlag.withdrawal_restrictions in flags
        assert not flags & BAD_FLAGS
        if protocol == "Asseto":
            assert VaultFlag.review_needed in flags
        elif protocol == "ETH Strategy":
            assert VaultFlag.long_duration in flags
    assert get_vault_special_flags("0x0000000000000000000000000000000000000001", "40acres") == set()


def test_plutus_note_is_chain_scoped_and_distinguishes_exit_routes() -> None:
    """Do not confuse a closed direct redemption route with a closed queue.

    Keep the documented queue timing attributed rather than a guaranteed exit.
    The note must not leak to a same-address deployment on another chain.

    :return: ``None`` after checking note routing and timing qualifications.
    """
    address = "0x58BFC95a864E18e8F3041d2fCd3418f48393FE6A"
    note = get_notes(address, chain_id=42161, protocol_name="Plutus")
    assert "up to seven days" in note
    assert "not a guaranteed payment deadline" in note
    assert "Trading Strategy's 30-day lockup figure is an estimate, not a guaranteed withdrawal deadline" in note
    assert "does not by itself establish that the queue is closed" in note
    assert "slippage" in note
    assert "https://docs.plutus.fi/products/plvassets" in note
    assert get_notes(address, chain_id=1, protocol_name="Plutus") is None


@pytest.fixture
def warning_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Create isolated metadata, migration resources and historical sentinels.

    Cover all reviewed targets, D2 strategy-note variants, an unknown D2 vault
    and unrelated rows, including same-address deployments on another chain.

    :param tmp_path: Pytest-owned temporary directory.
    :param monkeypatch: Environment isolation from legacy database overrides.
    :return: Pipeline data and single-migration resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {spec: {"Protocol": protocol, "_flags": {VaultFlag.deposit}, "_notes": "stale", "custom": 123} for spec, protocol in TARGETS}
    rows.update({spec: {"Protocol": "D2 Finance", "_flags": {VaultFlag.trading}, "_notes": "stale D2", "custom": 456} for spec in D2_TARGETS})
    rows.update({VaultSpec(137, spec.vault_address): {"Protocol": protocol, "_notes": "untouched"} for spec, protocol in TARGETS})
    rows.update(
        {
            spec: {
                "Protocol": "40acres",
                "_flags": {VaultFlag.deposit, VaultFlag.forty_acres_withdrawal_risk},
                "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="40acres"),
            }
            for spec in FORTY_ACRES_TARGETS
        }
    )
    rows[VaultSpec(999, "0x0000000000000000000000000000000000000001")] = {"Protocol": "40acres", "_flags": set(), "_notes": "untouched"}
    VaultDatabase(rows=rows, last_scanned_block={1: 12345, 999: 67890}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / SCRIPT_NAME).write_bytes(SCRIPT_PATH.read_bytes())
    return data_dir, scripts_dir


def test_warning_migration_preview_apply_and_retry(warning_migration: tuple[Path, Path]) -> None:
    """Refresh flags and complete strategy notes without touching other data.

    Preview changes no persisted bytes; apply backs up the database. Journalled
    reruns are skipped, and direct reruns also produce no field changes.

    :param warning_migration: Isolated pipeline and migration directories.
    :return: ``None`` after verifying preview, publication inputs and rerun safety.
    """
    data_dir, scripts_dir = warning_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    for spec, protocol in TARGETS:
        before.rows[spec].update(
            _flags={VaultFlag.deposit} | get_vault_special_flags(spec.vault_address, protocol),
            _notes=get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol),
        )
    for spec in D2_TARGETS:
        note = D2Vault(web3=None, spec=spec, features={ERC4626Feature.d2_like}).get_notes()
        before.rows[spec].update(
            _flags={VaultFlag.trading} | get_vault_special_flags(spec.vault_address, "D2 Finance"),
            _notes=note,
        )
        if spec == D2_TARGETS[2]:
            assert note == SUBVAULT
            assert VaultFlag.subvault in after.rows[spec]["_flags"]
        else:
            assert "**Deposits and withdrawals are closed during the trading phase.**" in note
            assert f"https://d2.finance/strategies/{spec.vault_address}" in note
    for spec in FORTY_ACRES_TARGETS:
        assert after.rows[spec] == before.rows[spec]
        assert after.rows[spec]["_flags"] == {VaultFlag.deposit, VaultFlag.forty_acres_withdrawal_risk}
    assert after == before
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["changed_rows"] == len(TARGETS) + len(D2_TARGETS)
    assert entry["other_data"]["updated_fields"] == 2 * (len(TARGETS) + len(D2_TARGETS))
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(data_dir, False, script_name=SCRIPT_NAME)
    runner._execute(script, context)
    assert context.other_data["changed_rows"] == 0
    assert context.other_data["updated_fields"] == 0
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol"])
def test_warning_migration_rejects_invalid_inventory(warning_migration: tuple[Path, Path], state: str) -> None:
    """Validate all named targets before modifying any staged metadata.

    Break the final target so earlier valid targets cannot hide a partial
    staged update. The persisted database must remain byte-for-byte intact.

    :param warning_migration: Isolated pipeline and migration directories.
    :param state: Missing row or conflicting protocol identity.
    :return: ``None`` after checking staging and persistence invariants.
    """
    data_dir, _ = warning_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    target = TARGETS[-1][0]
    if state == "missing":
        del database.rows[target]
    else:
        database.rows[target]["Protocol"] = "Other"
    database.write(path)
    original = path.read_bytes()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(data_dir, False, script_name=SCRIPT_NAME)
    with pytest.raises(ValueError, match="missing" if state == "missing" else "Unexpected protocol"):
        runner._execute(script, context)
    assert context.metadata() == database
    assert path.read_bytes() == original
    assert not list(data_dir.glob("*.bak*"))
