"""Lighter withdrawal-time operator fees in metadata, Parquet and JSON."""

import datetime
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from eth_defi.lighter.constants import LIGHTER_ETHEREUM, LIGHTER_ROBINHOOD, LighterAPIConfig
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase, LighterDailyPriceRow
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.vault import fetch_all_pools, fetch_pool_detail
from eth_defi.lighter.vault_data_export import build_raw_prices_dataframe, create_lighter_pool_row, merge_into_vault_database
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.vaultdb import VaultDatabase

#: Daily observations in the deterministic three-month source history.
HISTORY_DAYS = 91


def _store_pool(
    db: LighterDailyMetricsDatabase,
    deployment: LighterAPIConfig,
    account_index: int,
    operator_fee: float | None,
    period_return: float = 0.2,
) -> None:
    """Store a deterministic pool with 91 daily prices.

    The same account index can be reused across deployments to exercise the
    fee join without changing the source prices.

    :param db: Isolated file-backed database.
    :param deployment: Pool deployment and denomination.
    :param account_index: Deployment-local pool identifier.
    :param operator_fee: Current fee in percentage points, or unknown.
    :param period_return: Gross return across the full price history.
    :return: None; inserts metadata and daily observations.
    """
    start = datetime.datetime.fromisoformat("2026-01-01")
    db.upsert_pool_metadata(
        deployment=deployment.slug,
        account_index=account_index,
        name="Fee test pool",
        is_llp=operator_fee == 0.0,
        status=0,
        operator_fee=operator_fee,
        total_asset_value=100_000.0,
        created_at=start,
    )
    db.upsert_daily_prices(
        deployment.slug,
        [
            LighterDailyPriceRow(
                account_index=account_index,
                date=start.date() + datetime.timedelta(days=offset),
                share_price=1.0 + period_return * offset / (HISTORY_DAYS - 1),
                tvl=100_000.0,
                daily_return=0.0,
                annual_percentage_yield=0.0,
                total_shares=100_000,
                cumulative_pool_inflow=None,
                cumulative_pool_outflow=None,
                written_at=start + datetime.timedelta(days=offset),
            )
            for offset in range(HISTORY_DAYS)
        ],
    )


@pytest.mark.parametrize(
    ("gross_return", "operator_fee", "expected_net"),
    [(0.2, 30.0, 0.14), (-0.2, 30.0, -0.2), (0.2, 0.0, 0.2), (0.2, None, None)],
)
def test_lighter_fee_export_roundtrip(tmp_path: Path, gross_return: float, operator_fee: float | None, expected_net: float | None) -> None:
    """Refresh legacy fees and deduct them once from profitable investments.

    Exercise the real metadata merger, cleaning, Parquet writer and JSON row
    exporter; losses and a zero-fee LLP control retain the gross return.

    :param tmp_path: Isolated database and export directory.
    :param gross_return: Source share-price return.
    :param operator_fee: Current operator fee in percentage points.
    :param expected_net: Expected investor return after withdrawal.
    :return: None; verifies persisted fees and exported return calculations.
    """
    db = LighterDailyMetricsDatabase(tmp_path / "lighter.duckdb")
    try:
        _store_pool(db, LIGHTER_ETHEREUM, 42, operator_fee, gross_return)
        spec, legacy_row = create_lighter_pool_row(42, "Fee test pool", None, 100_000.0, None, operator_fee=operator_fee, is_llp=operator_fee == 0.0)
        legacy_row["_fees"].fee_mode = VaultFeeMode.internalised_skimming
        metadata_path = tmp_path / "vault-metadata-db.pickle"
        legacy_db = VaultDatabase()
        legacy_db.rows[spec] = legacy_row
        legacy_db.write(metadata_path)

        merge_into_vault_database(db, metadata_path)
        vault_db = VaultDatabase.read(metadata_path)
        assert vault_db.rows[spec]["_fees"].fee_mode == VaultFeeMode.externalised
        assert get_vault_fee_mode("Lighter", spec.vault_address) == VaultFeeMode.externalised

        raw = build_raw_prices_dataframe(db)
        raw_path = tmp_path / "raw.parquet"
        VaultHistoricalRead.write_uncleaned_parquet(raw, raw_path)
        restored_raw = pd.read_parquet(raw_path)
        if operator_fee is None:
            assert restored_raw["performance_fee"].isna().all()
        else:
            assert restored_raw["performance_fee"].tolist() == pytest.approx([operator_fee / 100] * len(raw))
        assert restored_raw["share_price"].tolist() == pytest.approx(raw["share_price"].tolist())

        cleaned = process_raw_vault_scan_data(vault_db, restored_raw, logger=lambda _: None)
        cleaned_path = tmp_path / "cleaned.parquet"
        cleaned.to_parquet(cleaned_path)
        restored_cleaned = pd.read_parquet(cleaned_path)
        assert len(restored_cleaned) == len(raw)
        if operator_fee is None:
            assert restored_cleaned["performance_fee"].isna().all()
        else:
            assert restored_cleaned["performance_fee"].tolist() == pytest.approx([operator_fee / 100] * len(raw))
        assert restored_cleaned["share_price"].tolist() == pytest.approx(raw["share_price"].tolist())

        returns = calculate_hourly_returns_for_all_vaults(restored_cleaned)
        metrics = calculate_lifetime_metrics(returns, vault_db)
        exported = export_lifetime_row(metrics.iloc[0])
        assert exported["fee_mode"] == "externalised"
        assert exported["fee_internalised"] is False
        assert exported["lifetime_return"] == pytest.approx(gross_return)
        if operator_fee is None:
            assert vault_db.rows[spec]["Perf fee"] is None
            assert exported["gross_fees"]["performance"] is None
            assert exported["net_fees"]["performance"] is None
            assert exported["lifetime_return_net"] is None
            assert exported["cagr_net"] is None
            assert all(period["returns_net"] is None and period["cagr_net"] is None for period in exported["period_results"])
        else:
            assert exported["gross_fees"]["performance"] == pytest.approx(operator_fee / 100)
            assert exported["net_fees"]["performance"] == pytest.approx(operator_fee / 100)
            assert exported["lifetime_return_net"] == pytest.approx(expected_net)
            assert exported["cagr_net"] == pytest.approx((1 + expected_net) ** (1 / exported["years"]) - 1)
    finally:
        db.close()


@pytest.mark.parametrize(
    ("fee_fields", "expected_fee"),
    [({}, None), ({"operator_fee": None}, None), ({"operator_fee": "0"}, 0.0), ({"operator_fee": "30"}, 30.0)],
)
def test_lighter_api_preserves_unknown_operator_fee(fee_fields: dict[str, str | None], expected_fee: float | None) -> None:
    """Distinguish missing API fees from a reported zero fee.

    Exercise both listing and account parsers with the same source values so
    ingestion cannot fabricate a zero before nullable metadata is exported.

    :param fee_fields: API fee field, explicitly null or omitted when unknown.
    :param expected_fee: Parsed percentage points, or unknown.
    :return: None; verifies listing, detail and snapshot agree.
    """
    responses = [
        {"liquidity_pool_index": 42},
        {"public_pools": [{"account_index": 42, "name": "Test pool", "account_type": 2, **fee_fields}]},
        {"accounts": [{"account_index": 42, "name": "Test pool", "pool_info": fee_fields}]},
    ]
    with LighterSession() as session:
        session.get = Mock(side_effect=[Mock(**{"json.return_value": response}) for response in responses])
        pools = fetch_all_pools(session)
        detail = fetch_pool_detail(session, 42)
    assert len(pools) == 1
    assert pools[0].operator_fee == expected_fee
    assert detail.operator_fee == expected_fee
    assert detail.snapshot.operator_fee == expected_fee


def test_lighter_parquet_fees_are_deployment_scoped_and_nullable(tmp_path: Path) -> None:
    """Keep deployment-specific fees and unknown observations through Parquet.

    Two deployments deliberately reuse account 42. Unknown metadata and absent
    metadata must preserve prices without fabricating a zero performance fee.

    :param tmp_path: Isolated database and Parquet directory.
    :return: None; verifies the composite join and nullable fee round trip.
    """
    db = LighterDailyMetricsDatabase(tmp_path / "lighter.duckdb")
    try:
        _store_pool(db, LIGHTER_ETHEREUM, 42, 30.0)
        _store_pool(db, LIGHTER_ROBINHOOD, 42, 10.0)
        _store_pool(db, LIGHTER_ETHEREUM, 43, None)
        _store_pool(db, LIGHTER_ETHEREUM, 44, 30.0)
        db.con.execute("DELETE FROM pool_metadata WHERE deployment = ? AND account_index = ?", [LIGHTER_ETHEREUM.slug, 44])

        raw = build_raw_prices_dataframe(db)
        path = tmp_path / "fees.parquet"
        VaultHistoricalRead.write_uncleaned_parquet(raw, path)
        restored = pd.read_parquet(path)
        assert len(restored) == 4 * HISTORY_DAYS
        for deployment, account_index, expected_fee in [(LIGHTER_ETHEREUM, 42, 0.3), (LIGHTER_ROBINHOOD, 42, 0.1)]:
            pool_rows = restored.loc[restored["address"] == deployment.format_pool_address(account_index)]
            assert len(pool_rows) == HISTORY_DAYS
            assert pool_rows["performance_fee"].tolist() == pytest.approx([expected_fee] * HISTORY_DAYS)
        for account_index in (43, 44):
            pool_rows = restored.loc[restored["address"] == LIGHTER_ETHEREUM.format_pool_address(account_index)]
            assert len(pool_rows) == HISTORY_DAYS
            assert pool_rows["performance_fee"].isna().all()
    finally:
        db.close()
