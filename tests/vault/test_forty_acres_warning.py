"""Keep 40acres warnings visible and refresh only the reviewed deployments."""

from pathlib import Path

import pytest

from eth_defi.research.vault_metrics import apply_bad_flag_check
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Independent expected scope for the persisted warning.
REVIEWED = (
    VaultSpec(8453, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"),
    VaultSpec(10, "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957"),
    VaultSpec(43114, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7"),
    VaultSpec(43114, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd"),
)
#: Liquid 40acres pool excluded from the warning inventory.
HYDREX = VaultSpec(8453, "0x16edb4dfc1d3368d051370699edfb280e9a1b474")


@pytest.mark.parametrize("spec", REVIEWED)
def test_forty_acres_warning_is_visible_and_sourced(spec: VaultSpec) -> None:
    """Expose a sourced warning without blacklisting a reviewed vault.

    The generic protocol path must resolve the dated note for each deployment
    and leave the flag outside catalogue exclusion rules.

    :param spec: Reviewed deployment identity.
    :return: ``None`` after checking visibility and material disclosures.
    """
    flags = get_vault_special_flags(spec.vault_address.upper(), "40acres")
    assert flags == {VaultFlag.forty_acres_withdrawal_risk}
    assert not flags & BAD_FLAGS
    assert not is_flagged_vault(spec.vault_address, "40acres")
    note = get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="40acres", include_defaults=False)
    assert note is not None
    assert get_notes(spec.vault_address, chain_id=1, protocol_name="40acres", include_defaults=False) is None
    assert apply_bad_flag_check(VaultTechnicalRisk.low, note, flags) == (VaultTechnicalRisk.low, note, flags)
    assert "2 November 2026" in note
    assert "outstanding loans" in note
    assert "cannot demand immediate loan repayment" in note
    assert "bad debt and losses" in note
    for source in ("https://docs.40acres.finance/lending", "https://docs.40acres.finance/risk-disclosure", "https://docs.40acres.finance/contracts"):
        assert f"]({source})" in note
    assert f"/address/{spec.vault_address})" in note
    if spec.chain_id in {10, 8453}:
        assert "https://40acresfinance.substack.com/p/40-acres-lender-update" in note
        assert "https://40acresfinance.substack.com/p/aero-migration-what-40acres-users" in note
        assert "withdrawal timetable for USDC lenders" in note
    if spec == REVIEWED[-1]:
        assert "1,581 USDC" in note
        assert "635,055 USDC" in note
        assert "$222,000 to $224,000" in note
        assert "not a verified sale price" in note


def test_hydrex_does_not_inherit_withdrawal_warning() -> None:
    """Do not turn four researched warnings into a protocol-wide exclusion.

    Hydrex had substantial available USDC in the reviewed snapshot.

    :return: ``None`` after verifying unchanged manual classification.
    """
    assert get_vault_special_flags(HYDREX.vault_address, "40acres") == set()
    assert get_notes(HYDREX.vault_address, chain_id=HYDREX.chain_id, protocol_name="40acres", include_defaults=False) is None
    assert not is_flagged_vault(HYDREX.vault_address, "40acres")


@pytest.fixture
def warning_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the real numbered migration from other pending repairs.

    Use the runner's test seam and temporary storage; no production state is
    read or changed.

    :param tmp_path: Temporary test workspace.
    :param monkeypatch: Environment isolation for pipeline paths.
    :return: Directory containing only the reviewed migration source.
    """
    monkeypatch.delenv("VAULT_DB", raising=False)
    monkeypatch.delenv("VAULT_DB_PATH", raising=False)
    directory = tmp_path / "scripts"
    directory.mkdir()
    migration = next(spec for spec in runner.discover_migrations() if spec.name == "022-40acres-withdrawal-risk.py")
    (directory / migration.name).write_bytes(migration.source)
    rows = {spec: {"Protocol": "40acres", "_flags": {VaultFlag.deposit}, "_notes": "old", "custom": 42} for spec in REVIEWED}
    rows[HYDREX] = {"Protocol": "40acres", "_flags": {VaultFlag.deposit}, "_notes": "Keep Hydrex"}
    rows[VaultSpec(1, REVIEWED[0].vault_address)] = {"Protocol": "Other deployment", "_notes": "Keep other chain"}
    VaultDatabase(rows=rows, last_scanned_block={8453: 123}).write(tmp_path / "vault-metadata-db.pickle")
    return directory


def test_warning_migration_preview_apply_and_idempotency(tmp_path: Path, warning_migration: Path) -> None:
    """Persist only reviewed fields while preserving flags and historical files.

    Exercise preview, apply and repeated execution through the real runner.

    :param tmp_path: Isolated pipeline directory.
    :param warning_migration: Selected migration resource directory.
    :return: ``None`` after asserting preservation and publication inputs.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    original = VaultDatabase.read(path)
    historical = tmp_path / "vault-prices-1h.parquet"
    historical.write_bytes(b"preserve price history without opening it")
    reader_state = tmp_path / "vault-reader-state-1h.pickle"
    reader_state.write_bytes(b"preserve reader cursor")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert len(runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=warning_migration, dry_run=True)) == 1
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert len(runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=warning_migration, dry_run=False)) == 1
    database = VaultDatabase.read(path)
    assert database.last_scanned_block == original.last_scanned_block
    for spec, row in database.rows.items():
        if spec in REVIEWED:
            assert row == original.rows[spec] | {
                "_flags": {VaultFlag.deposit, VaultFlag.forty_acres_withdrawal_risk},
                "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="40acres"),
            }
            assert not row["_flags"] & BAD_FLAGS
        else:
            assert row == original.rows[spec]
    assert historical.read_bytes() == before[historical.name]
    assert reader_state.read_bytes() == before[reader_state.name]
    applied = path.read_bytes()
    assert runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=warning_migration, dry_run=False) == []
    assert path.read_bytes() == applied
    # Exercise field-level idempotency even without the successful journal entry.
    (tmp_path / "migration.json").unlink()
    runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=warning_migration, dry_run=False)
    assert path.read_bytes() == applied


@pytest.mark.parametrize("invalid", ["missing", "protocol"])
def test_warning_migration_rejects_invalid_inventory(tmp_path: Path, warning_migration: Path, invalid: str) -> None:
    """Reject a bad final target before committing any earlier target.

    A missing or conflicting reviewed row must leave persisted metadata intact.

    :param tmp_path: Isolated pipeline directory.
    :param warning_migration: Selected migration resource directory.
    :param invalid: Missing-row or mismatched-protocol case.
    :return: ``None`` after checking the hard failure and unchanged data.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if invalid == "missing":
        del database.rows[REVIEWED[-1]]
    else:
        database.rows[REVIEWED[-1]]["Protocol"] = "Unexpected protocol"
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=r"missing|Unexpected protocol"):
        runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=warning_migration, dry_run=False)
    assert path.read_bytes() == before
