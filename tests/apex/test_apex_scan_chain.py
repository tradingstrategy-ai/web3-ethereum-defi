"""ApeX all-chain scanner adapter tests."""

# ruff: noqa: DTZ001

import datetime
from pathlib import Path

import pytest

from eth_defi.apex.metrics import ApexMetricsDatabase, ApexScanResult
from eth_defi.apex.vault import ApexVaultSummary
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.scan_all_chains import ChainResult
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_MAX_WORKERS = 3
EXPECTED_VAULT_COUNT = 7


def test_scan_apex_fn_closes_resources_and_merges_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the native adapter with a deterministic public-reader result.

    The wrapper must use the configured DuckDB path, close both resource
    owners, and make the fetched ApeX identity visible in shared metadata.
    """
    observed_at = datetime.datetime(2026, 7, 23, 12)
    vault_id = "2044287989957394432"
    pool_closed = False

    class FakePool:
        """Track context-managed session-pool closure."""

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, *_: object) -> None:
            nonlocal pool_closed
            pool_closed = True

    def fake_apex_run_scan(
        _pool: FakePool,
        database: ApexMetricsDatabase,
        *,
        max_workers: int,
    ) -> ApexScanResult:
        """Populate the real database without making network requests."""
        assert max_workers == EXPECTED_MAX_WORKERS
        database.apply_ranking(
            (
                ApexVaultSummary(
                    vault_id=vault_id,
                    synthetic_address=f"apex-vault-{vault_id}",
                    reported_ethereum_address=None,
                    name="ApeX test vault",
                    description="Fixture",
                    status="VAULT_IN_PROCESS",
                    vault_type="NOT_COLLECT_VAULT",
                    share_price=1.0,
                    tvl=100.0,
                    share_count=100.0,
                    created_at=observed_at,
                    source_updated_at=observed_at,
                    finished_at=None,
                    max_amount=None,
                    purchase_fee_rate_raw=None,
                    share_profit_ratio_raw=None,
                ),
            ),
            observed_at,
            manage_disappearance=True,
        )
        return ApexScanResult(
            observed_at=observed_at,
            discovered_vaults=1,
            selected_vaults=1,
            attempted_histories=0,
            successful_histories=0,
            failed_histories=0,
        )

    monkeypatch.setattr(scan_all_chains, "create_apex_session_pool", lambda **_: FakePool())
    monkeypatch.setattr(scan_all_chains, "apex_run_scan", fake_apex_run_scan)

    database_path = tmp_path / "apex-vaults.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    result = scan_all_chains.scan_apex_fn(
        max_workers=EXPECTED_MAX_WORKERS,
        db_path=database_path,
        vault_db_path=vault_db_path,
    )

    metadata = VaultDatabase.read(vault_db_path)
    spec = VaultSpec(chain_id=9995, vault_address=f"apex-vault-{vault_id}")
    assert result.status == "success"
    assert result.vault_count == 1
    assert result.price_rows == 1
    assert result.price_scan_ok is True
    assert spec in metadata.rows
    assert database_path.exists()
    assert pool_closed is True


def test_run_scan_tick_schedules_apex_and_advances_cycle_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute an ApeX-only all-chain tick through the scheduler branch.

    A successful native adapter result must reach the dashboard result map and
    persist the scheduler completion callback exactly once.
    """
    saved_items: list[str] = []
    captured_paths: list[Path] = []

    def fake_scan_apex_fn(
        max_workers: int,
        db_path: Path,
        vault_db_path: Path,
    ) -> ChainResult:
        """Capture orchestration arguments and return a completed scan."""
        assert max_workers == EXPECTED_MAX_WORKERS
        assert vault_db_path == tmp_path / "vault-metadata-db.pickle"
        captured_paths.append(db_path)
        return ChainResult(
            name="ApeX",
            status="success",
            vault_count=EXPECTED_VAULT_COUNT,
            vault_scan_ok=True,
            price_scan_ok=True,
        )

    monkeypatch.setattr(scan_all_chains, "scan_apex_fn", fake_scan_apex_fn)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_, **__: None)

    apex_db_path = tmp_path / "apex-vaults.duckdb"
    results = scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=["ApeX"],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=True,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=EXPECTED_MAX_WORKERS,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        apex_db_path=apex_db_path,
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        on_item_success=saved_items.append,
    )

    assert results["ApeX"].vault_count == EXPECTED_VAULT_COUNT
    assert captured_paths == [apex_db_path]
    assert saved_items == ["ApeX"]
