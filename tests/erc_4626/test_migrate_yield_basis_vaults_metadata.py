"""Tests for the YieldBasis metadata-only migration."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS


class _TokenCache:
    """Record metadata-cache lifecycle calls without accessing SQLite."""

    def __init__(self) -> None:
        """Initialise a cache lifecycle recorder."""

        self.committed = False
        self.closed = False

    def commit(self) -> None:
        """Record the persistent migration cache flush."""

        self.committed = True

    def close(self) -> None:
        """Record the migration cache cleanup."""

        self.closed = True


def _load_migration_module() -> ModuleType:
    """Load the hyphenated migration script as a Python module.

    The production entry point intentionally remains a directly executable
    script, so the test loads it by file path rather than changing its import
    surface.

    :return:
        Imported YieldBasis metadata-migration module.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "migrate-yield-basis-vaults-metadata.py"
    module_spec = importlib.util.spec_from_file_location("migrate_yield_basis_vaults_metadata", script_path)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _create_legacy_database() -> VaultDatabase:
    """Create reviewed YieldBasis rows with the obsolete Factory-ID suffix.

    The extra row proves the migration's normal catalogue write leaves unrelated
    metadata untouched.

    :return:
        In-memory vault metadata cache with four legacy YieldBasis names.
    """

    rows = {
        VaultSpec(1, review.lt_address.lower()): {
            "Name": f"yb-LP {review.asset_symbol} · market {review.market_id}",
            "Protocol": "YieldBasis",
        }
        for review in YIELD_BASIS_ACTIVE_MARKETS.values()
    }
    rows[VaultSpec(1, "0x0000000000000000000000000000000000000001")] = {
        "Name": "Unrelated vault",
        "Protocol": "Other",
    }
    return VaultDatabase(rows=rows)


def test_metadata_migration_reconciles_legacy_display_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rename every legacy YieldBasis metadata row without touching other rows.

    The live Factory pre-scan and catalogue synchroniser are replaced with a
    deterministic equivalent so the test proves the migration's write and
    reporting behaviour without an RPC dependency.

    :param tmp_path:
        Isolated metadata-cache location used by the persistent migration run.
    :param monkeypatch:
        Pytest fixture used to replace network and token-cache dependencies.
    :return:
        None.
    """

    migration = _load_migration_module()
    database_path = tmp_path / "vault-metadata-db.pickle"
    _create_legacy_database().write(database_path)
    token_cache = _TokenCache()
    preparation = SimpleNamespace(factory_valid=True, review_required=())

    monkeypatch.setattr(migration, "read_json_rpc_url", lambda _chain_id: "https://rpc.example")
    monkeypatch.setattr(migration, "create_multi_provider_web3", lambda _url: object())
    monkeypatch.setattr(migration, "get_almost_latest_block_number", lambda _web3: 1)
    monkeypatch.setattr(migration, "TokenDiskCache", lambda _path: token_cache)
    monkeypatch.setattr(migration, "fetch_yield_basis_scan_preparation", lambda _web3, _block_number: preparation)

    def synchronise_names(*, vault_db: VaultDatabase, **_kwargs: object) -> SimpleNamespace:
        """Apply the production catalogue's reviewed public display names.

        :param vault_db:
            In-memory vault database supplied by the migration.
        :return:
            Minimal successful catalogue synchronisation report.
        """

        for review in YIELD_BASIS_ACTIVE_MARKETS.values():
            vault_db.rows[VaultSpec(1, review.lt_address.lower())]["Name"] = f"yb-LP {review.asset_symbol}"
        return SimpleNamespace(products=4, inserted=0, updated=4)

    monkeypatch.setattr(migration, "fetch_and_sync_yield_basis_vault_catalogue", synchronise_names)

    result = migration.migrate_metadata(database_path=database_path, token_cache_path=tmp_path / "tokens.sqlite", dry_run=False)

    assert result == (4, 0, 4, 4)
    assert token_cache.committed is True
    assert token_cache.closed is True
    migrated_database = VaultDatabase.read(database_path)
    assert migration.count_legacy_market_display_names(migrated_database) == 0
    for review in YIELD_BASIS_ACTIVE_MARKETS.values():
        assert migrated_database.rows[VaultSpec(1, review.lt_address.lower())]["Name"] == f"yb-LP {review.asset_symbol}"
    assert migrated_database.rows[VaultSpec(1, "0x0000000000000000000000000000000000000001")]["Name"] == "Unrelated vault"
