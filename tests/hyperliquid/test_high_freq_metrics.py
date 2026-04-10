"""Integration test: high-frequency Hyperliquid vault metrics pipeline.

Tests the HF pipeline end-to-end:

1. Create HF database and scan a real vault
2. Verify DuckDB has timestamp-precision rows
3. Verify resume: scan twice with/without cutoff, confirm overlap upsert
4. Verify lifecycle: tombstone and disappeared vault handling
5. Verify Phase 1 proxy fix: _make_request() routes through post_info()
6. Verify DuckDB cursor-per-call avoids the shared-connection thread race

Requires network access to the Hyperliquid API for scans; the
concurrency regression test is offline and uses synthetic rows only.
"""

import datetime
from unittest.mock import patch

import pytest
from joblib import Parallel, delayed

from eth_defi.hyperliquid.deposit import fetch_vault_deposits
from eth_defi.hyperliquid.high_freq_metrics import (
    HyperliquidHighFreqMetricsDatabase,
    HyperliquidHighFreqPriceRow,
    fetch_and_store_vault_high_freq,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import HyperliquidVault, fetch_all_vaults
from eth_defi.compat import native_datetime_utc_now


@pytest.mark.timeout(180)
def test_high_freq_metrics_scan_and_resume(tmp_path):
    """Scan a vault in HF mode, then rescan to verify resume and overlap.

    1. Create HF database and scan a vault with a cutoff timestamp
    2. Verify DuckDB has timestamp-precision rows (not date)
    3. Rescan without cutoff — verify new rows added beyond cutoff
    4. Verify overlapping timestamps have identical share prices (COALESCE)
    5. Verify written_at and leader metrics columns are present
    """
    duckdb_path = tmp_path / "hf-metrics.duckdb"
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    session = create_hyperliquid_session()

    # 1. Find the vault in the bulk listing
    all_vaults = list(fetch_all_vaults(session))
    target_summary = None
    for s in all_vaults:
        if s.vault_address.lower() == vault_address.lower():
            target_summary = s
            break

    assert target_summary is not None, f"Vault {vault_address} not found in bulk listing"

    # 2. First scan with cutoff
    cutoff = datetime.datetime(2025, 12, 15, 0, 0, 0)

    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_vault_high_freq(
            session,
            db,
            target_summary,
            cutoff_timestamp=cutoff,
            flow_backfill_days=0,
        )
        assert result, "First scan: failed to fetch and store vault"
        db.save()

        # 3. Verify timestamp precision
        first_df = db.get_vault_high_freq_prices(vault_address)
        assert len(first_df) > 0, "First scan produced no data"
        assert "timestamp" in first_df.columns, "Expected timestamp column, not date"

        first_last_ts = db.get_vault_last_timestamp(vault_address)
        assert first_last_ts is not None
        assert first_last_ts <= cutoff, f"First scan stored data beyond cutoff: {first_last_ts}"

        first_prices = dict(zip(first_df["timestamp"], first_df["share_price"]))
        first_count = len(first_df)

        # 4. Second scan without cutoff — should add data beyond cutoff
        result = fetch_and_store_vault_high_freq(
            session,
            db,
            target_summary,
            cutoff_timestamp=None,
            flow_backfill_days=0,
        )
        assert result, "Second scan: failed to fetch and store vault"
        db.save()

        second_df = db.get_vault_high_freq_prices(vault_address)
        second_count = len(second_df)
        second_last_ts = db.get_vault_last_timestamp(vault_address)

        assert second_count > first_count, f"Second scan did not add data: first={first_count}, second={second_count}"
        assert second_last_ts > cutoff, f"Second scan did not extend beyond cutoff: {second_last_ts}"

        # 5. Overlapping timestamps should have identical share prices
        second_prices = dict(zip(second_df["timestamp"], second_df["share_price"]))
        for ts, first_price in first_prices.items():
            second_price = second_prices.get(ts)
            assert second_price is not None, f"Timestamp {ts} missing in second run"
            assert first_price == pytest.approx(second_price, rel=1e-10), f"Share price mismatch at {ts}"

        # 6. Verify columns
        assert "leader_fraction" in second_df.columns
        assert "leader_commission" in second_df.columns
        assert "written_at" in second_df.columns
        assert second_df["written_at"].notna().all()

    finally:
        db.close()


@pytest.mark.timeout(30)
def test_high_freq_lifecycle(tmp_path):
    """Verify tombstone and disappeared vault handling.

    1. Create HF database with synthetic vault data
    2. Call mark_vaults_disappeared() with empty known set
    3. Verify tombstone row was written with timestamp key
    4. Verify tombstone carries forward share_price
    """
    duckdb_path = tmp_path / "hf-lifecycle.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    try:
        # 1. Insert synthetic metadata and price row
        db.upsert_vault_metadata(
            vault_address="0xdeadbeef",
            name="Test Vault",
            leader="0xleader",
            description=None,
            is_closed=False,
            relationship_type="normal",
            create_time=None,
            commission_rate=None,
            follower_count=10,
            tvl=50000.0,
            apr=0.05,
        )

        now = native_datetime_utc_now()
        price_row = HyperliquidHighFreqPriceRow(
            vault_address="0xdeadbeef",
            timestamp=now - datetime.timedelta(hours=8),
            share_price=1.25,
            tvl=50000.0,
            cumulative_pnl=5000.0,
            data_source="api",
            written_at=now - datetime.timedelta(hours=8),
        )
        db.upsert_high_freq_prices([price_row])
        db.save()

        # 2. Mark vault as disappeared (empty known set)
        db.mark_vaults_disappeared(known_addresses=set())

        # 3. Verify tombstone row was written
        prices_df = db.get_vault_high_freq_prices("0xdeadbeef")
        assert len(prices_df) == 2, f"Expected 2 rows (original + tombstone), got {len(prices_df)}"

        tombstone = prices_df[prices_df["data_source"] == "tombstone"]
        assert len(tombstone) == 1, "Expected exactly 1 tombstone row"

        # 4. Verify tombstone carries forward share_price and has tvl=0
        assert tombstone.iloc[0]["share_price"] == pytest.approx(1.25)
        assert tombstone.iloc[0]["tvl"] == pytest.approx(0.0)
        assert tombstone.iloc[0]["cumulative_pnl"] == pytest.approx(5000.0)

    finally:
        db.close()


@pytest.mark.timeout(30)
def test_post_info_proxy_fix():
    """Verify _make_request() and fetch_vault_deposits() route through post_info().

    1. Create a session
    2. Patch session.post_info and call vault.fetch_info()
    3. Assert post_info was called (not raw post)
    """
    session = create_hyperliquid_session()

    # We mock post_info to return a valid-looking response
    import requests

    mock_response = requests.Response()
    mock_response.status_code = 200

    # Test _make_request via HyperliquidVault
    vault = HyperliquidVault(
        session=session,
        vault_address="0x3df9769bbbb335340872f01d8157c779d73c6ed0",
    )

    # Test _make_request via vault.fetch_info()
    with patch.object(session, "post_info", return_value=mock_response) as mock_post_info:
        mock_post_info.return_value._content = b'{"name": "test", "portfolio": [], "followers": []}'
        try:
            vault.fetch_info()
        except Exception:
            pass  # Response parsing may fail, but we only care that post_info was called
        assert mock_post_info.called, "_make_request() should call session.post_info(), not session.post()"

    # Test fetch_vault_deposits() also routes through post_info()
    mock_response_deposits = requests.Response()
    mock_response_deposits.status_code = 200
    mock_response_deposits._content = b"[]"

    with patch.object(session, "post_info", return_value=mock_response_deposits) as mock_post_info:
        try:
            list(
                fetch_vault_deposits(
                    session,
                    "0x3df9769bbbb335340872f01d8157c779d73c6ed0",
                    start_time=datetime.datetime(2025, 1, 1),
                    end_time=datetime.datetime(2025, 1, 2),
                )
            )
        except Exception:
            pass
        assert mock_post_info.called, "fetch_vault_deposits() should call session.post_info(), not session.post()"


@pytest.mark.timeout(60)
def test_high_freq_concurrent_db_writes(tmp_path):
    """Regression: HF database must survive many concurrent worker threads.

    The HF scan orchestrator drives a shared ``HyperliquidHighFreqMetricsDatabase``
    from ``joblib.Parallel(backend="threading")``.  Before the cursor-per-call
    fix, multiple threads calling ``execute(...).fetchone()`` on the same
    underlying DuckDB connection would clobber each other's result sets and
    raise ``Invalid Input Error: No open result set``.

    This test reproduces that workload offline with synthetic rows and
    asserts that the full upsert + read cycle completes cleanly and the
    data ends up on disc.

    1. Open a fresh HF database in a temp dir
    2. Fire many worker threads that each: upsert metadata, read
       ``get_vault_last_timestamp``, upsert a batch of price rows, and
       read ``get_vault_high_freq_prices``
    3. Assert no exception was raised and the row counts match
    """
    duckdb_path = tmp_path / "hf-concurrent.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    num_vaults = 40
    rows_per_vault = 20
    base_time = datetime.datetime(2026, 1, 1, 0, 0, 0)

    def _worker(vault_index: int) -> int:
        # Distinct address per worker so upserts don't collide.
        vault_address = f"0x{vault_index:040x}"

        # Metadata write — uses self.con.cursor() under the fix.
        db.upsert_vault_metadata(
            vault_address=vault_address,
            name=f"Vault {vault_index}",
            leader="0x0000000000000000000000000000000000000001",
            description=None,
            is_closed=False,
            relationship_type="normal",
            create_time=None,
            commission_rate=None,
            follower_count=1,
            tvl=1000.0,
            apr=0.1,
        )

        # Read: this was the hot spot for the race.
        last_ts = db.get_vault_last_timestamp(vault_address)
        assert last_ts is None, f"Fresh vault {vault_index} should have no rows"

        # Bulk upsert of synthetic HF rows.
        rows = [
            HyperliquidHighFreqPriceRow(
                vault_address=vault_address,
                timestamp=base_time + datetime.timedelta(hours=i),
                share_price=1.0 + 0.001 * i,
                tvl=1000.0 + i,
                cumulative_pnl=float(i),
                data_source="api",
                written_at=base_time,
            )
            for i in range(rows_per_vault)
        ]
        db.upsert_high_freq_prices(rows)

        # Another read that hits the same connection.
        df = db.get_vault_high_freq_prices(vault_address)
        return len(df)

    try:
        # Match the scan orchestrator: threading backend, many workers.
        results = Parallel(n_jobs=16, backend="threading")(delayed(_worker)(i) for i in range(num_vaults))
        db.save()

        # Every worker must have seen its full batch.
        assert all(n == rows_per_vault for n in results), f"Row counts per worker: {results}"

        # And the aggregated totals on disc must match what we wrote.
        all_prices = db.get_all_high_freq_prices()
        assert len(all_prices) == num_vaults * rows_per_vault
        assert all_prices["vault_address"].nunique() == num_vaults
    finally:
        db.close()
