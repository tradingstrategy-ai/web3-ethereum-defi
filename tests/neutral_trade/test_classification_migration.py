"""Persist risk and fee corrections without touching history or EVM products."""

from pathlib import Path

import pytest

from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID
from eth_defi.neutral_trade.vault_data_export import FEE_BASIS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.risk import VAULT_SPECIFIC_RISK, VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner


@pytest.mark.parametrize("unassessed_override", [False, True])
def test_classification_refresh_preview_apply_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, unassessed_override: bool) -> None:
    """Refresh native classifications while preserving prices and stricter risks.

    Include unknown rates and explicit zero schedules, plus unrelated native
    and EVM sentinels. Preview is byte-preserving and reapplication is skipped.

    :param tmp_path: Isolated pipeline files and migration resources.
    :param monkeypatch: Remove legacy path overrides from the private environment.
    :param unassessed_override: Exercise an explicitly unassessed address.
    :return: ``None`` after preview, apply, preservation and retry checks.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "067-neutral-trade-risk-and-fee-mode.py")
    (scripts / script.name).write_bytes(script.source)
    native = [VaultSpec(SOLANA_DATASET_CHAIN_ID, "solana-vault-" + f"{number:064x}") for number in range(3)]
    if unassessed_override:
        monkeypatch.setitem(VAULT_SPECIFIC_RISK, native[2].vault_address, None)
    schedules = (FeeData(None, 0.01, 0.3, 0, 0.003), FeeData(None, 0, 0, 0, 0), FeeData(None, 0, 0, None, 0))
    rows = {spec: {"Protocol": "Neutral Trade", "Name": "Fixture", "_fees": fees, "_risk": VaultTechnicalRisk.low, "_neutral_trade": {"fee_basis": "old", "history_verified": False}} for spec, fees in zip(native, schedules, strict=True)}
    rows[native[2]]["_risk"] = VaultTechnicalRisk.severe
    evm = VaultSpec(1, "0x" + "12" * 20)
    other = VaultSpec(SOLANA_DATASET_CHAIN_ID, "solana-vault-" + "ff" * 32)
    rows[evm] = {"Protocol": "Accountable", "_fees": FeeData(VaultFeeMode.internalised_skimming, 0.01, 0.2, 0, 0), "_risk": VaultTechnicalRisk.low}
    rows[other] = {"Protocol": "Other", "_risk": None}
    path = data / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows, last_scanned_block={1: 123}).write(path)
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "neutral-trade-vaults.duckdb"):
        (data / name).write_bytes(b"Preserve history")
    original = {file.name: file.read_bytes() for file in data.iterdir()}
    options = {"data_dir": data, "scripts_dir": scripts, "provenance": {"in_docker": False}}
    runner.run_pending_migrations(dry_run=True, **options)
    assert {file.name: file.read_bytes() for file in data.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    result = VaultDatabase.read(path)
    assert result.last_scanned_block == {1: 123}
    assert result.rows[evm] == rows[evm]
    assert result.rows[other] == rows[other]
    assert get_vault_risk("Neutral Trade") == VaultTechnicalRisk.high
    for index, spec in enumerate(native):
        row = result.rows[spec]
        assert row["_fees"].fee_mode == (VaultFeeMode.feeless if index == 1 else VaultFeeMode.externalised)
        assert row["_fees"].management == schedules[index].management
        assert row["_fees"].performance == schedules[index].performance
        assert row["_fees"].deposit == schedules[index].deposit
        assert row["_fees"].withdraw == schedules[index].withdraw
        assert row["_risk"] == (VaultTechnicalRisk.severe if index == 2 else None)  # noqa: PLR2004 - third fixture carries a stricter override.
        assert (row["_risk"] or get_vault_risk("Neutral Trade")) in {VaultTechnicalRisk.high, VaultTechnicalRisk.severe}
        assert row["_neutral_trade"] == {"fee_basis": FEE_BASIS, "history_verified": False}
    assert not result.rows[native[2]]["_fees"].can_calculate_investor_net_performance()
    assert all((data / name).read_bytes() == content for name, content in original.items() if name != path.name)
    applied = {file.name: file.read_bytes() for file in data.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data.iterdir()} == applied
