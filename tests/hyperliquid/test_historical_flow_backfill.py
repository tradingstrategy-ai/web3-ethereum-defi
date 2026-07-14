"""Test manual Hypercore historical ledger-flow backfills."""

import datetime
import runpy
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase, HyperliquidDailyPriceRow

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "hyperliquid" / "backfill-historical-vault-flows.py"


def test_update_historical_daily_flows(tmp_path) -> None:
    """A deep backfill replaces a legacy zero withdrawal without changing prices.

    Older parser versions stored zero for ``vaultWithdraw.usdc`` even when
    ``netWithdrawnUsd`` contained the realised cash flow. The manual backfill
    must replace that zero and make no-event dates explicit.
    """
    address = "0x1764dd740aba4195643bbb6a44648e0306b00cfa"
    db = HyperliquidDailyMetricsDatabase(tmp_path / "hyperliquid-vaults.duckdb")
    try:
        db.upsert_daily_prices(
            [
                HyperliquidDailyPriceRow(
                    vault_address=address,
                    date=datetime.date(2026, 2, day),
                    share_price=price,
                    tvl=assets,
                    cumulative_pnl=pnl,
                    daily_withdrawal_count=0,
                    daily_withdrawal_usd=0.0,
                )
                for day, price, assets, pnl in [
                    (4, 1.304877, 19_127.624245, 7_531.634245),
                    (5, 26.035410, 10_126.256026, 6_144.896026),
                    (6, 1.20, 10_126.256026, 6_144.896026),
                ]
            ]
        )

        updated = db.update_historical_daily_flows(
            address,
            daily_flows={datetime.date(2026, 2, 5): (0, 4, 0.0, 7_614.648567)},
            start_date=datetime.date(2026, 2, 4),
            end_date=datetime.date(2026, 2, 6),
        )
        prices = db.get_vault_daily_prices(address).set_index("date")
        expected_updated_rows = 3
        expected_withdrawal_count = 4

        assert updated == expected_updated_rows
        assert prices.loc[pd.Timestamp("2026-02-04"), "daily_withdrawal_usd"] == pytest.approx(0.0)
        assert prices.loc[pd.Timestamp("2026-02-05"), "daily_withdrawal_count"] == expected_withdrawal_count
        assert prices.loc[pd.Timestamp("2026-02-05"), "daily_withdrawal_usd"] == pytest.approx(7_614.648567)
        assert prices.loc[pd.Timestamp("2026-02-05"), "share_price"] == pytest.approx(26.035410)
        assert prices.loc[pd.Timestamp("2026-02-06"), "daily_withdrawal_usd"] == pytest.approx(0.0)
    finally:
        db.close()


def test_historical_flow_candidate_selection() -> None:
    """Explicit addresses and autodetection select deferred observations."""
    select_candidates = runpy.run_path(str(SCRIPT_PATH))["select_historical_flow_candidates"]
    cleaned = pd.DataFrame(
        {
            "address": ["0xaaa", "0xbbb", "0xccc"],
            "timestamp": pd.to_datetime(["2026-02-01", "2026-02-02", "2026-02-03"]),
            "hypercore_repair_status": ["deferred_hf_nav", "deferred_hf_nav", "repaired_hf"],
        }
    )

    explicit = select_candidates(cleaned, {"0xaaa"}, autodetect=False)
    autodetected = select_candidates(cleaned, set(), autodetect=True)

    assert explicit["address"].tolist() == ["0xaaa"]
    assert autodetected["address"].tolist() == ["0xaaa", "0xbbb"]
    with pytest.raises(RuntimeError, match="exactly one"):
        select_candidates(cleaned, set(), autodetect=False)
    with pytest.raises(RuntimeError, match="exactly one"):
        select_candidates(cleaned, {"0xaaa"}, autodetect=True)


def test_create_iterated_duckdb_backup(tmp_path) -> None:
    """Repeated database updates create numbered main and WAL backups."""
    create_backup = runpy.run_path(str(SCRIPT_PATH))["create_iterated_duckdb_backup"]
    db_path = tmp_path / "hyperliquid-vaults.duckdb"
    wal_path = Path(f"{db_path}.wal")
    db_path.write_bytes(b"database-v1")
    wal_path.write_bytes(b"wal-v1")

    first_backup = create_backup(db_path)
    db_path.write_bytes(b"database-v2")
    wal_path.write_bytes(b"wal-v2")
    second_backup = create_backup(db_path)

    assert first_backup.name == "hyperliquid-vaults.duckdb.bak-0001"
    assert second_backup.name == "hyperliquid-vaults.duckdb.bak-0002"
    assert first_backup.read_bytes() == b"database-v1"
    assert Path(f"{first_backup}.wal").read_bytes() == b"wal-v1"
    assert second_backup.read_bytes() == b"database-v2"
    assert Path(f"{second_backup}.wal").read_bytes() == b"wal-v2"
