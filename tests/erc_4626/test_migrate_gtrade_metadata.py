"""File-backed checks for the fixed-scope Gains-family metadata repair."""

import datetime
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VAULT_FLAGS_AND_NOTES, VaultFlag
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_REVIEWED_ROWS = 24


@pytest.fixture
def migration() -> ModuleType:
    """Load the operational script without invoking its entrypoint."""
    script = Path(__file__).resolve().parents[2] / "scripts/erc-4626/migrate-gtrade-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_gtrade_metadata", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def metadata_path(tmp_path: Path) -> Path:
    """Create every reviewed deployment plus unrelated rows and scan state."""
    rows = {}
    for chain_id, address in REVIEWED_GAINS_VAULTS:
        spec = VaultSpec(chain_id, address)
        rows[spec] = {
            "Protocol": "Gains Network",
            "Address": address,
            "Link": f"https://routescan.io/address/{address}",
            "_notes": None,
            "_strategy_tags": {StrategyTag.amm},
            "_curator_slug": "gains-network",
            "_flags": set(),
            "_manager_name": "Preserve existing manager",
            "_detection_data": ERC4262VaultDetection(
                chain=chain_id,
                address=address,
                first_seen_at_block=100,
                first_seen_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC).replace(tzinfo=None),
                updated_at=datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC).replace(tzinfo=None),
                deposit_count=10,
                redeem_count=1,
                features={ERC4626Feature.gains_like},
            ),
        }
    rows[VaultSpec(1, NOVA_POOL_ADDRESS)] = {"Protocol": "Other protocol", "_notes": "Leave unchanged"}
    rows[VaultSpec(42161, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")] = {"Protocol": "Ostium", "_notes": "Leave unchanged"}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows, leads={VaultSpec(1, "0x0000000000000000000000000000000000000001"): "existing lead"}, last_scanned_block={42161: 123456}).write(path)
    return path


def test_dry_run_never_writes(migration: ModuleType, metadata_path: Path) -> None:
    """Dry-run defaults preserve exact pickle bytes and create no backup."""
    original = metadata_path.read_bytes()
    result = migration.migrate_gtrade_metadata(metadata_path)
    assert result.inspected_rows == EXPECTED_REVIEWED_ROWS
    assert len(result.updates) == EXPECTED_REVIEWED_ROWS
    assert result.missing == ()
    assert result.backup_path is None
    assert metadata_path.read_bytes() == original
    assert not tuple(metadata_path.parent.glob("*.bak-gtrade-metadata*"))


def test_apply_preserves_state_and_is_idempotent(migration: ModuleType, metadata_path: Path) -> None:
    """Only reviewed public metadata changes; the original is recoverable."""
    original_bytes = metadata_path.read_bytes()
    original = VaultDatabase.read(metadata_path)
    price_file = metadata_path.parent / "vault-prices-1h.parquet"
    price_file.write_bytes(b"historical prices must remain byte-identical")
    reader_file = metadata_path.parent / "reader-state.pickle"
    reader_file.write_bytes(b"reader boundaries must remain byte-identical")
    result = migration.migrate_gtrade_metadata(metadata_path, dry_run=False)
    assert result.backup_path.read_bytes() == original_bytes
    database = VaultDatabase.read(metadata_path)
    staging = database.rows[VaultSpec(42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7")]
    assert staging["_strategy_tags"] is None
    assert staging["_curator_slug"] is None
    assert "could not be verified" in staging["_notes"]
    assert database.leads == original.leads
    assert database.last_scanned_block == original.last_scanned_block
    changed_fields = {"Link", "_notes", "_strategy_tags", "_curator_slug"}
    for spec, old_row in original.rows.items():
        new_row = database.rows[spec]
        assert {key: value for key, value in old_row.items() if key not in changed_fields} == {key: value for key, value in new_row.items() if key not in changed_fields}
        if (spec.chain_id, spec.vault_address) not in REVIEWED_GAINS_VAULTS:
            assert new_row == old_row
    assert price_file.read_bytes() == b"historical prices must remain byte-identical"
    assert reader_file.read_bytes() == b"reader boundaries must remain byte-identical"
    applied_bytes = metadata_path.read_bytes()
    rerun = migration.migrate_gtrade_metadata(metadata_path, dry_run=False)
    assert rerun.updates == ()
    assert rerun.backup_path is None
    assert metadata_path.read_bytes() == applied_bytes


@pytest.mark.parametrize("conflict", ["protocol", "address", "chain", "feature"])
def test_conflict_aborts_before_any_write(migration: ModuleType, metadata_path: Path, conflict: str) -> None:
    """Conflicting identity anywhere in the target set aborts the whole repair."""
    database = VaultDatabase.read(metadata_path)
    spec = VaultSpec(80094, "0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c")
    row = database.rows[spec]
    if conflict == "protocol":
        row["Protocol"] = "Ostium"
    elif conflict == "address":
        row["Address"] = NOVA_POOL_ADDRESS
    elif conflict == "chain":
        row["_detection_data"] = replace(row["_detection_data"], chain=1)
    else:
        row["_detection_data"] = replace(row["_detection_data"], features={ERC4626Feature.gains_like, ERC4626Feature.ostium_like})
    database.write(metadata_path)
    original = metadata_path.read_bytes()
    with pytest.raises(ValueError):
        migration.migrate_gtrade_metadata(metadata_path, dry_run=False)
    assert metadata_path.read_bytes() == original
    assert not tuple(metadata_path.parent.glob("*.bak-gtrade-metadata*"))


def test_missing_historical_rows_are_reported(migration: ModuleType, metadata_path: Path) -> None:
    """The repair reports absent historical deployments without inventing rows."""
    database = VaultDatabase.read(metadata_path)
    absent = VaultSpec(999, "0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8")
    del database.rows[absent]
    database.write(metadata_path)
    result = migration.migrate_gtrade_metadata(metadata_path, dry_run=False)
    assert result.inspected_rows == EXPECTED_REVIEWED_ROWS - 1
    assert result.missing == (absent,)
    assert absent not in VaultDatabase.read(metadata_path).rows


def test_manual_warning_preserved(migration: ModuleType, metadata_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The operational repair follows the same warning precedence as scanning."""
    monkeypatch.setitem(VAULT_FLAGS_AND_NOTES, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0", (VaultFlag.illiquid, "Manual withdrawal warning"))
    migration.migrate_gtrade_metadata(metadata_path, dry_run=False)
    assert VaultDatabase.read(metadata_path).rows[VaultSpec(42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0")]["_notes"] == "Manual withdrawal warning"
