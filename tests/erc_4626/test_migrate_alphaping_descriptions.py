"""Exercise AlphaPing's metadata-only repair and public description export."""

import datetime
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.handwritten_metadata import ALPHAPING_IPOR_VAULT_METADATA, ALPHAPING_VAULT_METADATA
from eth_defi.vault.vaultdb import VaultDatabase

FOREX_SPEC = VaultSpec(1, "0x153bd1abe60104bd46aa05a27fa12d1346d64a57")
UNRELATED_SPEC = VaultSpec(8453, FOREX_SPEC.vault_address)
REVIEWED_VAULT_COUNT = 36


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    """Load the hyphenated repair script without running its entry point.

    :return: Importable migration module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "erc-4626" / "migrate-alphaping-descriptions.py"
    spec = importlib.util.spec_from_file_location("migrate_alphaping_descriptions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vault_db() -> VaultDatabase:
    """Create stale reviewed rows plus a same-address deployment on another chain.

    :return: Database with unrelated fields and scanner state to preserve.
    """
    rows = {
        VaultSpec(chain_id, address): {
            "Address": address,
            "Name": metadata.name,
            "Symbol": "reviewedVault",
            "Denomination": "USDC",
            "Protocol": "IPOR Fusion" if (chain_id, address) in ALPHAPING_IPOR_VAULT_METADATA else "Morpho",
            "_description": None,
            "_short_description": "Stale summary.",
            "_notes": "Keep the risk warning.",
            "_flags": {VaultFlag.illiquid},
            "_deposit_closed_reason": "Keep existing status.",
            "custom_field": "Retain this value.",
        }
        for (chain_id, address), metadata in ALPHAPING_VAULT_METADATA.items()
    }
    rows[FOREX_SPEC]["Name"] = "Acuto"
    rows[UNRELATED_SPEC] = {"Protocol": "Morpho", "_description": "Unrelated description.", "_short_description": "Unrelated summary."}
    return VaultDatabase(rows=rows, leads={UNRELATED_SPEC: "keep"}, last_scanned_block={1: 26_250_213})


def test_alphaping_migration_preserves_other_fields_and_is_idempotent(migration: ModuleType, vault_db: VaultDatabase) -> None:
    """Repair every reviewed row while preserving names, warnings and scan state."""
    before = deepcopy(vault_db)
    result = migration.migrate_alphaping_descriptions(vault_db, dry_run=False)
    assert result.inspected_rows == REVIEWED_VAULT_COUNT
    assert result.missing_specs == ()
    assert len(result.updates) == REVIEWED_VAULT_COUNT
    for (chain_id, address), metadata in ALPHAPING_VAULT_METADATA.items():
        spec = VaultSpec(chain_id, address)
        assert vault_db.rows[spec] == {**before.rows[spec], "_description": metadata.description, "_short_description": metadata.short_description}
    assert vault_db.rows[FOREX_SPEC]["Name"] == "Acuto"
    assert vault_db.rows[UNRELATED_SPEC] == before.rows[UNRELATED_SPEC]
    assert vault_db.leads == before.leads
    assert vault_db.last_scanned_block == before.last_scanned_block
    assert migration.migrate_alphaping_descriptions(vault_db, dry_run=False).updates == ()


def test_alphaping_migration_dry_run_and_missing_rows(migration: ModuleType, vault_db: VaultDatabase) -> None:
    """Report missing entries without inserting rows or mutating a dry run."""
    missing = VaultSpec(1, "0xb530a1b5259a71187f1d69acf0488f102637a3ed")
    del vault_db.rows[missing]
    before = deepcopy(vault_db.rows)
    result = migration.migrate_alphaping_descriptions(vault_db, dry_run=True)
    assert result.inspected_rows == REVIEWED_VAULT_COUNT - 1
    assert result.missing_specs == (missing,)
    assert len(result.updates) == REVIEWED_VAULT_COUNT - 1
    assert vault_db.rows == before
    applied = migration.migrate_alphaping_descriptions(vault_db, dry_run=False)
    assert len(applied.updates) == REVIEWED_VAULT_COUNT - 1
    assert missing not in vault_db.rows


@pytest.mark.parametrize("target", [FOREX_SPEC, VaultSpec(1, "0x29d322dd088e9b9d1416f43188954f08748fafbb")])
@pytest.mark.parametrize("dry_run", [True, False])
def test_alphaping_migration_validates_all_protocols_before_mutation(migration: ModuleType, vault_db: VaultDatabase, target: VaultSpec, *, dry_run: bool) -> None:
    """Reject a misclassified row before writing any earlier valid target."""
    vault_db.rows[target]["Protocol"] = "Unexpected protocol"
    before = deepcopy(vault_db.rows)
    with pytest.raises(ValueError, match="unexpected protocols"):
        migration.migrate_alphaping_descriptions(vault_db, dry_run=dry_run)
    assert vault_db.rows == before


def test_alphaping_migration_cli_defaults_to_dry_run_and_writes_atomically(
    migration: ModuleType,
    vault_db: VaultDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exercise environment configuration against a real file-backed pickle."""
    del vault_db.rows[VaultSpec(1, "0xb530a1b5259a71187f1d69acf0488f102637a3ed")]
    path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(path)
    preserved_files = {
        tmp_path / "vault-prices-1h.parquet": b"preserve historical prices",
        tmp_path / "vault-reader-state-1h.pickle": b"preserve reader state",
    }
    for preserved_path, content in preserved_files.items():
        preserved_path.write_bytes(content)
    before = path.read_bytes()
    monkeypatch.setenv("VAULT_DB_PATH", str(path))
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path / "unused"))
    monkeypatch.delenv("DRY_RUN", raising=False)
    # Keep pytest's log capture handler when exercising the command entry point.
    monkeypatch.setattr(migration, "setup_console_logging", lambda **_: None)
    migration.main()
    capsys.readouterr()
    assert path.read_bytes() == before
    assert "is missing; skipping" in caplog.text

    monkeypatch.setenv("DRY_RUN", "false")
    migration.main()
    capsys.readouterr()
    stored = VaultDatabase.read(path)
    assert stored.rows[FOREX_SPEC]["_description"] == ALPHAPING_VAULT_METADATA[1, FOREX_SPEC.vault_address].description
    assert stored.rows[FOREX_SPEC]["_notes"] == "Keep the risk warning."
    assert stored.leads == vault_db.leads
    assert stored.last_scanned_block == vault_db.last_scanned_block
    after = path.read_bytes()
    migration.main()
    capsys.readouterr()
    assert path.read_bytes() == after
    assert all(preserved_path.read_bytes() == content for preserved_path, content in preserved_files.items())


def test_alphaping_migration_cli_rejects_invalid_dry_run(migration: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mistyped dry-run value must never silently enable persistent mode."""
    monkeypatch.setenv("DRY_RUN", "treu")
    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        migration.main()


def test_alphaping_repaired_copy_reaches_public_metrics_json(migration: ModuleType, vault_db: VaultDatabase) -> None:
    """Pass repaired copy through real lifetime metrics and JSON serialisation."""
    migration.migrate_alphaping_descriptions(vault_db, dry_run=False)
    row = vault_db.rows[FOREX_SPEC]
    row.update(
        {
            "Share token": "Acuto",
            "Link": "https://app.morpho.org/ethereum/vault/" + FOREX_SPEC.vault_address,
            "_fees": FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=0.0, performance=0.1, deposit=0.0, withdraw=0.0),
            "_detection_data": ERC4262VaultDetection(
                chain=1,
                address=FOREX_SPEC.vault_address,
                first_seen_at_block=1,
                first_seen_at=datetime.datetime(2026, 9, 1),  # noqa: DTZ001 - Project timestamps use naive UTC.
                updated_at=datetime.datetime(2026, 10, 1),  # noqa: DTZ001 - Project timestamps use naive UTC.
                features={ERC4626Feature.morpho_v2_like},
                deposit_count=10,
                redeem_count=10,
            ),
        }
    )
    prices = pd.DataFrame(
        {
            "id": FOREX_SPEC.as_string_id(),
            "address": FOREX_SPEC.vault_address,
            "chain": 1,
            "block_number": range(35),
            "share_price": [1 + i * 0.0001 for i in range(35)],
            "total_assets": 100_000.0,
            "total_supply": 100_000.0,
            "event_count": 20,
        },
        index=pd.date_range("2026-09-01", periods=35, freq="D"),
    )
    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), {FOREX_SPEC: row})
    exported = json.loads(json.dumps(export_lifetime_row(metrics.iloc[0]), allow_nan=False))
    metadata = ALPHAPING_VAULT_METADATA[1, FOREX_SPEC.vault_address]
    assert exported["description"] == metadata.description
    assert exported["short_description"] == metadata.short_description
    assert exported["name"] == "Acuto"
    assert exported["notes"] == "Keep the risk warning."
    assert "illiquid" in exported["flags"]
