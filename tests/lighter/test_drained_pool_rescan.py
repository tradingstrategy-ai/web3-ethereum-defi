"""Network-free tests for Lighter drained-pool rescans and dashboard timestamps."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.lighter import daily_metrics
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_ETHEREUM, LIGHTER_ROBINHOOD
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase, LighterDailyPriceRow, run_daily_scan
from eth_defi.lighter.vault import LighterPoolSummary
from eth_defi.vault.scan_all_chains import _load_last_timestamps

#: Pool above the scan filter in the current listing.
ACTIVE_POOL = 101

#: Pool whose latest stored row is above the filter but which has drained.
DRAINED_POOL = 102

#: Pool already stored below the filter, so no further scans are needed.
SETTLED_POOL = 103

#: Pool never stored and below the filter.
UNKNOWN_POOL = 104

#: Pools whose latest stored row has no usable TVL.
NULL_TVL_POOL = 105
NAN_TVL_POOL = 106


def _summary(account_index: int, tvl: float) -> LighterPoolSummary:
    """Create a minimal bulk-listing pool summary.

    :param account_index:
        Pool account index.
    :param tvl:
        Current listing TVL.
    :return:
        Pool summary.
    """
    return LighterPoolSummary(
        account_index=account_index,
        name=f"Pool {account_index}",
        annual_percentage_yield=0.0,
        sharpe_ratio=None,
        operator_fee=None,
        total_asset_value=tvl,
        total_shares=1,
        status=0,
        account_type=2,
        master_account_index=1,
        created_at=None,
        l1_address="0x0000000000000000000000000000000000000002",
    )


def _price_row(account_index: int, date: datetime.date, tvl: float | None) -> LighterDailyPriceRow:
    """Create one stored daily price row.

    :param account_index:
        Pool account index.
    :param date:
        Row date.
    :param tvl:
        Stored TVL.
    :return:
        Daily price row.
    """
    return LighterDailyPriceRow(
        account_index=account_index,
        date=date,
        share_price=0.001,
        tvl=tvl,
        daily_return=0.0,
        annual_percentage_yield=0.0,
        total_shares=1,
        cumulative_pool_inflow=None,
        cumulative_pool_outflow=None,
        written_at=datetime.datetime(2026, 9, 1),
    )


def test_run_daily_scan_rescans_pools_drained_since_last_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool stored above ``min_tvl`` is rescanned once it drops below it."""
    db_path = tmp_path / "lighter.duckdb"
    db = LighterDailyMetricsDatabase(db_path)
    try:
        db.upsert_daily_prices(
            LIGHTER_ETHEREUM.slug,
            [
                _price_row(DRAINED_POOL, datetime.date(2026, 8, 1), 500.0),
                _price_row(DRAINED_POOL, datetime.date(2026, 8, 2), 400_000.0),
                _price_row(SETTLED_POOL, datetime.date(2026, 8, 1), 400_000.0),
                _price_row(SETTLED_POOL, datetime.date(2026, 8, 2), 50.0),
                _price_row(NULL_TVL_POOL, datetime.date(2026, 8, 1), 5_000.0),
                _price_row(NULL_TVL_POOL, datetime.date(2026, 8, 2), None),
                _price_row(NAN_TVL_POOL, datetime.date(2026, 8, 1), 5_000.0),
                _price_row(NAN_TVL_POOL, datetime.date(2026, 8, 2), float("nan")),
            ],
        )
        assert db.get_pools_with_latest_tvl_at_least(LIGHTER_ETHEREUM.slug, 1_000) == {DRAINED_POOL}
        assert db.get_pools_with_latest_tvl_at_least(LIGHTER_ROBINHOOD.slug, 1_000) == set()
    finally:
        db.close()

    listing = [
        _summary(ACTIVE_POOL, 5_000.0),
        _summary(DRAINED_POOL, 49.0),
        _summary(SETTLED_POOL, 10.0),
        _summary(UNKNOWN_POOL, 20.0),
        _summary(NULL_TVL_POOL, 20.0),
        _summary(NAN_TVL_POOL, 20.0),
    ]
    processed: list[int] = []

    def fake_store(session: object, db: LighterDailyMetricsDatabase, summary: LighterPoolSummary, cutoff_date: object, timeout: float) -> bool:
        del session, db, cutoff_date, timeout
        processed.append(summary.account_index)
        return True

    monkeypatch.setattr(daily_metrics, "fetch_all_pools", lambda session, timeout: listing)
    monkeypatch.setattr(daily_metrics, "_process_pool_worker", fake_store)
    monkeypatch.setattr(daily_metrics, "collect_lighter_pool_observations", lambda *args, **kwargs: 0)

    session = SimpleNamespace(deployment=LIGHTER_ETHEREUM)
    db = run_daily_scan(session, db_path=db_path, min_tvl=1_000, max_workers=1)
    db.close()

    assert sorted(processed) == [ACTIVE_POOL, DRAINED_POOL]


def test_load_last_timestamps_splits_lighter_deployments(tmp_path: Path) -> None:
    """Both Lighter dashboard rows get their own last-data timestamp."""
    path = tmp_path / "prices.parquet"
    table = pa.table(
        {
            "chain": pa.array([1, LIGHTER_CHAIN_ID, LIGHTER_CHAIN_ID, LIGHTER_CHAIN_ID], type=pa.uint32()),
            "address": ["0x0000000000000000000000000000000000000001", "lighter-pool-1", "lighter-pool-2", "lighter-pool-robinhood-3"],
            "timestamp": pa.array(
                [
                    datetime.datetime(2026, 10, 26, 10),
                    datetime.datetime(2026, 10, 25, 0),
                    datetime.datetime(2026, 10, 26, 0),
                    datetime.datetime(2026, 10, 24, 0),
                ],
                type=pa.timestamp("ms"),
            ),
        }
    )
    pq.write_table(table, path)

    timestamps = _load_last_timestamps(path)

    assert timestamps["ethereum"] == "2026-10-26 10:00"
    assert timestamps["lighter ethereum"] == "2026-10-26 00:00"
    assert timestamps["lighter robinhood"] == "2026-10-24 00:00"


def test_load_last_timestamps_keeps_chains_without_address_column(tmp_path: Path) -> None:
    """A failed Lighter split does not blank the other chains' timestamps."""
    path = tmp_path / "prices.parquet"
    table = pa.table(
        {
            "chain": pa.array([1], type=pa.uint32()),
            "timestamp": pa.array([datetime.datetime(2026, 10, 26, 10)], type=pa.timestamp("ms")),
        }
    )
    pq.write_table(table, path)

    timestamps = _load_last_timestamps(path)

    assert timestamps == {"ethereum": "2026-10-26 10:00"}
