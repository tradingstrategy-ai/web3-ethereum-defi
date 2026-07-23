"""Lighter ownership snapshots and cumulative-flow export tests."""

import datetime
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase, LighterDailyPriceRow
from eth_defi.lighter.vault import fetch_pool_daily_pnl_history
from eth_defi.lighter.vault_data_export import _derive_daily_flow_columns, build_raw_prices_dataframe, create_lighter_pool_row, merge_into_uncleaned_parquet
from eth_defi.research.vault_metrics import _calculate_netflow_metrics, calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data


def _make_daily_row(
    date: datetime.date,
    cumulative_pool_inflow: float | None,
    cumulative_pool_outflow: float | None,
) -> LighterDailyPriceRow:
    """Create a stable synthetic Lighter daily observation."""
    return LighterDailyPriceRow(
        account_index=42,
        date=date,
        share_price=1.0,
        tvl=1_000.0,
        daily_return=0.0,
        annual_percentage_yield=0.0,
        total_shares=1_000,
        cumulative_pool_inflow=cumulative_pool_inflow,
        cumulative_pool_outflow=cumulative_pool_outflow,
        written_at=datetime.datetime(2025, 1, 10),
    )


def test_lighter_pnl_history_excludes_future_placeholder() -> None:
    """Keep API future placeholders out of the daily source history."""
    known_timestamp = int(datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc).timestamp())
    future_timestamp = int(datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
    response = Mock()
    response.json.return_value = {
        "pnl": [
            {"timestamp": known_timestamp, "pool_total_shares": 1_000, "pool_inflow": 100.0, "pool_outflow": 10.0},
            {"timestamp": future_timestamp, "pool_total_shares": 2_000, "pool_inflow": 200.0, "pool_outflow": 20.0},
        ]
    }
    session = Mock(api_url="https://example.invalid")
    session.get.return_value = response

    history = fetch_pool_daily_pnl_history(session, account_index=42)

    assert list(history) == [datetime.date(2025, 1, 2)]
    assert history[datetime.date(2025, 1, 2)].cumulative_pool_inflow == pytest.approx(100.0)
    assert session.get.call_args.kwargs["params"]["ignore_transfers"] == "false"


def test_lighter_database_preserves_cumulative_counters_and_ownership(tmp_path) -> None:
    """Store raw Lighter accounting counters without fabricating event counts."""
    db = LighterDailyMetricsDatabase(tmp_path / "lighter.duckdb")
    try:
        db.upsert_pool_metadata(
            account_index=42,
            name="Test pool",
            total_shares=1_000,
            operator_shares=125,
        )
        db.upsert_daily_prices(
            [
                _make_daily_row(datetime.date(2025, 1, 1), 100.0, 0.0),
                _make_daily_row(datetime.date(2025, 1, 2), 125.0, 0.0),
                _make_daily_row(datetime.date(2025, 1, 3), 125.0, 20.0),
            ]
        )

        # A bounded re-scan with an unavailable counter must not erase source
        # data already retained for the same date.
        db.upsert_daily_prices([_make_daily_row(datetime.date(2025, 1, 2), None, None)])

        metadata = db.get_all_pool_metadata().iloc[0]
        assert metadata["total_shares"] == 1_000
        assert metadata["operator_shares"] == 125

        prices = db.get_pool_daily_prices(42)
        assert prices["total_shares"].tolist() == [1_000, 1_000, 1_000]
        assert prices["cumulative_pool_inflow"].tolist() == [100.0, 125.0, 125.0]
        assert prices["cumulative_pool_outflow"].tolist() == [0.0, 0.0, 20.0]

        raw = build_raw_prices_dataframe(db).sort_values("timestamp").reset_index(drop=True)
        assert raw["total_supply"].tolist() == [1_000, 1_000, 1_000]
        assert pd.isna(raw.loc[0, "daily_deposit_usd"])
        assert raw.loc[1, "daily_deposit_usd"] == pytest.approx(25.0)
        assert raw.loc[1, "daily_withdrawal_usd"] == pytest.approx(0.0)
        assert raw.loc[2, "daily_deposit_usd"] == pytest.approx(0.0)
        assert raw.loc[2, "daily_withdrawal_usd"] == pytest.approx(20.0)
        assert raw["daily_deposit_count"].isna().all()
        assert raw["daily_withdrawal_count"].isna().all()

        merged = merge_into_uncleaned_parquet(db, tmp_path / "vault-prices-1h.parquet")
        assert {"total_supply", "daily_deposit_usd", "daily_withdrawal_usd"} <= set(merged.columns)
    finally:
        db.close()


def test_lighter_flow_derivation_rejects_gaps_resets_and_current_day() -> None:
    """Leave invalid, incomplete, and provisional Lighter flow intervals unknown."""
    prices = pd.DataFrame(
        {
            "account_index": [42, 42, 42, 42, 42],
            "date": [
                datetime.date(2025, 1, 1),
                datetime.date(2025, 1, 2),
                datetime.date(2025, 1, 4),
                datetime.date(2025, 1, 5),
                datetime.date(2025, 1, 6),
            ],
            "cumulative_pool_inflow": [100.0, 120.0, 130.0, 5.0, 10.0],
            "cumulative_pool_outflow": [0.0, 0.0, 0.0, 0.0, 0.0],
        }
    )

    result = _derive_daily_flow_columns(prices, current_date=datetime.date(2025, 1, 6))

    assert pd.isna(result.loc[0, "daily_deposit_usd"])
    assert result.loc[1, "daily_deposit_usd"] == pytest.approx(20.0)
    assert pd.isna(result.loc[2, "daily_deposit_usd"]), "A gap must not be assigned to its closing day"
    assert pd.isna(result.loc[3, "daily_deposit_usd"]), "A counter reset must not become an outflow"
    assert pd.isna(result.loc[4, "daily_deposit_usd"]), "The current UTC day is provisional"


def test_lighter_operator_snapshot_is_metadata_only() -> None:
    """Retain ownership fields in the synthetic vault metadata row."""
    _, vault_row = create_lighter_pool_row(
        account_index=42,
        name="Test pool",
        description=None,
        tvl=1_000.0,
        created_at=datetime.datetime(2025, 1, 1),
        total_shares=1_000,
        operator_shares=125,
        ownership_updated_at=datetime.datetime(2025, 1, 2),
    )

    assert vault_row["_lighter_operator_shares"] == 125
    assert vault_row["_lighter_total_shares"] == 1_000
    assert vault_row["_lighter_operator_share_fraction"] == pytest.approx(0.125)
    assert vault_row["_lighter_ownership_updated_at"] == datetime.datetime(2025, 1, 2)


def test_amount_only_netflow_preserves_unknown_event_counts() -> None:
    """Export monetary Lighter flow without pretending it has event counts."""
    dates = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {
            "daily_deposit_usd": [10.0, 0.0, 5.0],
            "daily_withdrawal_usd": [0.0, 3.0, 0.0],
            "daily_deposit_count": [np.nan, np.nan, np.nan],
            "daily_withdrawal_count": [np.nan, np.nan, np.nan],
        },
        index=dates,
    )

    netflow = _calculate_netflow_metrics(prices, now_=dates[-1])
    assert netflow is not None
    week = next(row for row in netflow if row.period == "7d")
    assert week.data_complete
    assert week.deposit_count is None
    assert week.withdrawal_count is None
    assert week.deposit_usd == pytest.approx(15.0)
    assert week.withdrawal_usd == pytest.approx(3.0)
    assert week.net_flow_usd == pytest.approx(12.0)

    prices.loc[dates[-1], "daily_deposit_usd"] = np.nan
    incomplete = _calculate_netflow_metrics(prices, now_=dates[-1])
    assert incomplete is not None
    week = next(row for row in incomplete if row.period == "7d")
    assert not week.data_complete
    assert week.deposit_usd is None
    assert week.withdrawal_usd is None
    assert week.net_flow_usd is None


def test_lighter_ownership_reaches_metrics_json(tmp_path) -> None:
    """Expose the current Lighter ownership snapshot only in JSON extension data."""
    db = LighterDailyMetricsDatabase(tmp_path / "lighter.duckdb")
    start = datetime.date(2025, 1, 1)
    try:
        db.upsert_pool_metadata(
            account_index=42,
            name="Test pool",
            total_shares=1_000,
            operator_shares=125,
        )
        db.upsert_daily_prices(
            [
                LighterDailyPriceRow(
                    account_index=42,
                    date=start + datetime.timedelta(days=offset),
                    share_price=1.0 + offset * 0.0001,
                    tvl=1_000.0 + offset * 0.1,
                    daily_return=0.0001,
                    annual_percentage_yield=0.0,
                    total_shares=1_000,
                    cumulative_pool_inflow=1_000.0 + offset,
                    cumulative_pool_outflow=0.0,
                    written_at=datetime.datetime(2025, 4, 1),
                )
                for offset in range(91)
            ]
        )
        raw = build_raw_prices_dataframe(db)
        spec, vault_row = create_lighter_pool_row(
            account_index=42,
            name="Test pool",
            description=None,
            tvl=1_000.0,
            created_at=datetime.datetime(2025, 1, 1),
            total_shares=1_000,
            operator_shares=125,
            ownership_updated_at=datetime.datetime(2025, 4, 1),
        )
        cleaned = process_raw_vault_scan_data({spec: vault_row}, raw, logger=lambda _: None, display=lambda _: None)
        returns = calculate_hourly_returns_for_all_vaults(cleaned)
        metrics = calculate_lifetime_metrics(returns, {spec: vault_row})
        exported = export_lifetime_row(metrics.iloc[0])

        lighter = exported["other_data"]["lighter"]
        assert lighter["operator_shares"] == 125
        assert lighter["total_shares"] == 1_000
        assert lighter["operator_share_fraction"] == pytest.approx(0.125)
        assert lighter["ownership_updated_at"] == "2025-04-01T00:00:00"
    finally:
        db.close()
