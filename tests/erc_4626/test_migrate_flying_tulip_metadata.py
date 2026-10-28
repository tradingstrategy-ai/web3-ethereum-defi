"""Test targeted Flying Tulip metadata repairs without touching price state."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SSFTUSD_BY_CHAIN
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow


@pytest.fixture
def migration(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the operator script with deterministic provider and record reads.

    The replacement record intentionally omits stale fields so the tests
    detect accidental merging of obsolete synthetic or capacity metadata.

    :param monkeypatch:
        Fixture isolating network access.
    :return:
        Imported, network-isolated migration module.
    """
    path = Path(__file__).resolve().parents[2] / "eth_defi/vault_protocols/repairs/flying_tulip.py"
    spec = importlib.util.spec_from_file_location("migrate_flying_tulip_metadata", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_json_rpc_url", lambda chain_id: "test-provider")
    monkeypatch.setattr(module, "setup_console_logging", lambda **kwargs: None)
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda url: SimpleNamespace(eth=SimpleNamespace(block_number=123)))
    monkeypatch.setattr(module, "print", lambda *args, **kwargs: None, raising=False)

    def rebuild(web3: object, detection: ERC4262VaultDetection, block: int, cache: TokenDiskCache | None) -> VaultRow:
        """Return only the fresh scanner-owned fields.

        :param web3:
            Unused provider substitute.
        :param detection:
            Updated detection preserving discovery evidence.
        :param block:
            Unused scan block.
        :param cache:
            Unused token cache.
        :return:
            Minimal replacement record.
        """
        return {"Protocol": "Flying Tulip", "Name": "Reviewed staking vault", "Denomination": "ftUSD", "NAV": 1, "Address": detection.address, "_detection_data": detection, "_deposit_closed_reason": None, "features": detection.features}

    monkeypatch.setattr(module, "create_vault_scan_record", rebuild)
    return module


@pytest.fixture
def metadata_db() -> VaultDatabase:
    """Create stale records for both products and an unrelated vault.

    Discovery and progress evidence must survive replacement of the selected
    record, including when its cached protocol features were incorrect.

    :return:
        Isolated in-memory metadata database.
    """
    rows = {}
    for chain, address in (*FLYING_TULIP_SFTUSD_BY_CHAIN.items(), *FLYING_TULIP_SSFTUSD_BY_CHAIN.items()):
        detection = ERC4262VaultDetection(chain, address, 42, datetime.datetime(2026, 9, 14), set(), datetime.datetime(2026, 10, 1), 36, 5)
        rows[VaultSpec(chain, address)] = {"Protocol": "ERC-4626", "_detection_data": detection, "_deposit_closed_reason": "maxDeposit=0", "_external_reward_token": "stale"}
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    rows[unrelated] = {"Protocol": "Morpho", "manual_field": "preserved"}
    return VaultDatabase(rows=rows, leads={}, last_scanned_block={1: 12345})


def test_metadata_target_dry_run_and_safe_rerun(migration: ModuleType, metadata_db: VaultDatabase) -> None:
    """Refresh only ssftUSD, clearing stale fields and preserving discovery.

    Dry runs must not replace any row. Repeated application preserves the
    same classification, original discovery counts and unrelated row objects.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Mixed stale metadata database.
    :return:
        ``None`` after checking replacement boundaries.
    """
    target = VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    original_rows = metadata_db.rows.copy()
    migration.migrate_flying_tulip_metadata(metadata_db, dry_run=True, target=target)
    assert all(metadata_db.rows[spec] is row for spec, row in original_rows.items())
    for _ in range(2):
        result = migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False, target=target)
        assert result.migrated_rows == 1
        row = metadata_db.rows[target]
        assert row["features"] == {ERC4626Feature.flying_tulip_stable_like}
        assert "_external_reward_token" not in row
        assert row["_deposit_closed_reason"] is None
        detection = row["_detection_data"]
        assert detection.first_seen_at_block == 42
        assert detection.first_seen_at == datetime.datetime(2026, 9, 14)
        assert (detection.deposit_count, detection.redeem_count) == (36, 5)
        assert all(metadata_db.rows[spec] is old for spec, old in original_rows.items() if spec != target)
        assert metadata_db.last_scanned_block == {1: 12345}


def test_metadata_legacy_scope_and_filtered_sftusd(migration: ModuleType, metadata_db: VaultDatabase) -> None:
    """Retain the legacy three-sftUSD default and its external reward flags.

    An explicit sftUSD target must also resolve the correct variant rather
    than borrowing the new ssftUSD features.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Mixed stale metadata database.
    :return:
        ``None`` after checking both invocation forms.
    """
    stable = VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    original = metadata_db.rows[stable]
    assert migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False).migrated_rows == 3
    assert metadata_db.rows[stable] is original
    target = VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1])
    migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False, target=target)
    assert metadata_db.rows[target]["features"] == {ERC4626Feature.flying_tulip_like, ERC4626Feature.share_price_equivalence}


@pytest.mark.parametrize("chain,address", [(56, FLYING_TULIP_SSFTUSD_BY_CHAIN[1]), (1, "0x0000000000000000000000000000000000000002")])
def test_metadata_rejects_unreviewed_target(migration: ModuleType, metadata_db: VaultDatabase, chain: int, address: HexAddress) -> None:
    """Reject unknown chain/address pairs before any database mutation.

    Address matching alone must never opt an unreviewed chain into migration.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Mixed stale metadata database.
    :param chain:
        Unreviewed deployment chain.
    :param address:
        Address used for negative coverage.
    :return:
        ``None`` after checking validation.
    """
    with pytest.raises(ValueError, match="Unreviewed"):
        migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False, target=VaultSpec(chain, address))


def test_metadata_missing_target_and_staging_failure(migration: ModuleType, metadata_db: VaultDatabase) -> None:
    """Abort missing targets or invalid later rows before applying replacements.

    A failure after an earlier record was rebuilt must discard that staged
    replacement rather than partially updating the database.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Mixed stale metadata database.
    :return:
        ``None`` after checking all-or-nothing updates.
    """
    target = VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    del metadata_db.rows[target]
    with pytest.raises(ValueError, match="missing"):
        migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False, target=target)
    original = metadata_db.rows.copy()
    second = VaultSpec(56, FLYING_TULIP_SFTUSD_BY_CHAIN[56])
    metadata_db.rows[second] = {"_detection_data": None}
    with pytest.raises(ValueError, match="no persisted"):
        migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False)
    first = VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1])
    assert metadata_db.rows[first] is original[first]


def test_metadata_main_backup_and_environment(migration: ModuleType, metadata_db: VaultDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Back up the original pickle and modify no neighbouring price/state files.

    Persistent operations use an isolated directory, including repeated apply
    runs that must allocate distinct backups.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Mixed stale metadata database.
    :param tmp_path:
        Isolated persistent metadata directory.
    :param monkeypatch:
        Fixture configuring the operator environment.
    :return:
        ``None`` after checking persistence, backups and paired filters.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    metadata_db.write(path)
    before = path.read_bytes()
    state = tmp_path / "reader-state.pickle"
    prices = tmp_path / "vault-prices-1h.parquet"
    state.write_bytes(b"reader state")
    prices.write_bytes(b"price history")
    monkeypatch.setenv("VAULT_DB_PATH", str(path))
    monkeypatch.setenv("CHAIN_ID", "1")
    monkeypatch.setenv("VAULT_ADDRESS", FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    monkeypatch.setenv("DRY_RUN", "true")
    migration.main()
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*"))
    monkeypatch.setenv("DRY_RUN", "false")
    migration.main()
    backup = Path(f"{path}.bak-flying-tulip-metadata")
    assert backup.read_bytes() == before
    assert VaultDatabase.read(path).rows[VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])]["Protocol"] == "Flying Tulip"
    migration.main()
    assert Path(f"{backup}.1").exists()
    assert backup.read_bytes() == before
    assert state.read_bytes() == b"reader state"
    assert prices.read_bytes() == b"price history"
    monkeypatch.delenv("VAULT_ADDRESS")
    with pytest.raises(ValueError, match="supplied together"):
        migration.main()


@pytest.mark.parametrize("missing_field", ["Name", "Denomination", "NAV"])
def test_metadata_incomplete_scan_preserves_cache(migration: ModuleType, metadata_db: VaultDatabase, monkeypatch: pytest.MonkeyPatch, missing_field: str) -> None:
    """Reject partial RPC results rather than replacing existing metadata.

    Protocol identity alone does not prove a successful scan. Required asset
    and valuation fields must also be available before a replacement is staged.

    :param migration:
        Network-isolated operator script.
    :param metadata_db:
        Existing metadata database.
    :param monkeypatch:
        Fixture substituting a partially failed scan record.
    :param missing_field:
        Required scanner field omitted by the simulated provider failure.
    :return:
        ``None`` after checking rejection without mutation.
    """
    target = VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    original = metadata_db.rows[target]
    partial = {"Protocol": "Flying Tulip", "Name": "Stable vault", "Denomination": "ftUSD", "NAV": 1}
    del partial[missing_field]
    monkeypatch.setattr(migration, "create_vault_scan_record", lambda *args: partial)
    with pytest.raises(ValueError, match="incomplete metadata"):
        migration.migrate_flying_tulip_metadata(metadata_db, dry_run=False, target=target)
    assert metadata_db.rows[target] is original
