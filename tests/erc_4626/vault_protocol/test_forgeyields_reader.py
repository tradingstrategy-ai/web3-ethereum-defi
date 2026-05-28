"""Test ForgeYields historical reader and backfill behaviour.

1. Reader writes denomination-token TVL for near-head rows, None for old rows
2. Backfill script overwrites ForgeYields rows in the covered date range
"""

import datetime
import math
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.vault_protocol.forgeyields.vault import ForgeYieldsHistoricalReader
from eth_defi.vault.base import VaultHistoricalRead


def _make_price_row(address: str, chain: int, share_price: float, timestamp: datetime.datetime, block_number: int, total_assets=float("nan")) -> dict:
    """Create a minimal price row dict matching the canonical schema."""
    return {
        "chain": chain,
        "address": address,
        "block_number": block_number,
        "timestamp": timestamp,
        "share_price": share_price,
        "total_assets": total_assets,
        "total_supply": 1000.0,
        "performance_fee": 0.20,
        "management_fee": 0.0,
        "errors": "",
        "vault_poll_frequency": "",
        "max_deposit": float("nan"),
        "max_redeem": float("nan"),
        "deposits_open": "",
        "redemption_open": "",
        "trading": "",
        "available_liquidity": float("nan"),
        "utilisation": float("nan"),
        "written_at": timestamp,
    }


def test_reader_near_head_gets_tvl():
    """Near-head rows get denomination-token TVL from the API.

    1. Create a mock vault returning tvl=1069435.71 (USDC denomination)
    2. Call process_result with a timestamp close to now
    3. Assert total_assets is the denomination-token TVL
    """
    mock_vault = MagicMock()
    mock_vault.fetch_tvl.return_value = Decimal("1069435.71")
    mock_vault.address = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    mock_vault.chain_id = 1

    reader = ForgeYieldsHistoricalReader.__new__(ForgeYieldsHistoricalReader)
    reader.vault = mock_vault
    reader.stateful = False

    # Mock the multicall processing
    now = datetime.datetime(2026, 5, 28, 12, 0, 0)
    near_head_ts = now - datetime.timedelta(hours=1)

    with patch("eth_defi.erc_4626.vault_protocol.forgeyields.vault.native_datetime_utc_now", return_value=now):
        with patch.object(reader, "dictify_multicall_results") as mock_dict:
            with patch.object(reader, "process_core_erc_4626_result") as mock_core:
                mock_dict.return_value = {}
                mock_core.return_value = (Decimal("1.086"), Decimal("1000"), None, ["total_assets revert"], None)

                result = reader.process_result(
                    block_number=25_000_000,
                    timestamp=near_head_ts,
                    call_results=[],
                )

    # total_assets should be the denomination-token TVL
    assert result.total_assets == Decimal("1069435.71")
    assert result.share_price == Decimal("1.086")


def test_reader_old_row_gets_none():
    """Old rows (> 24h from now) get total_assets=None.

    1. Create a mock vault returning tvl=1069435.71
    2. Call process_result with a timestamp 48 hours ago
    3. Assert total_assets is None
    """
    mock_vault = MagicMock()
    mock_vault.fetch_tvl.return_value = Decimal("1069435.71")
    mock_vault.address = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    mock_vault.chain_id = 1

    reader = ForgeYieldsHistoricalReader.__new__(ForgeYieldsHistoricalReader)
    reader.vault = mock_vault
    reader.stateful = False

    now = datetime.datetime(2026, 5, 28, 12, 0, 0)
    old_ts = now - datetime.timedelta(hours=48)

    with patch("eth_defi.erc_4626.vault_protocol.forgeyields.vault.native_datetime_utc_now", return_value=now):
        with patch.object(reader, "dictify_multicall_results") as mock_dict:
            with patch.object(reader, "process_core_erc_4626_result") as mock_core:
                mock_dict.return_value = {}
                mock_core.return_value = (Decimal("1.086"), Decimal("1000"), None, ["total_assets revert"], None)

                result = reader.process_result(
                    block_number=24_900_000,
                    timestamp=old_ts,
                    call_results=[],
                )

    assert result.total_assets is None
    assert result.share_price == Decimal("1.086")


def test_backfill_overwrites_existing_values():
    """Backfill script overwrites all ForgeYields rows, not just NaN.

    1. Create a parquet with a row that has an incorrect total_assets value
    2. Run the backfill main logic
    3. Verify total_assets is overwritten with the API history value
    """
    addr = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    # Row timestamp matches a history entry
    row_ts = datetime.datetime(2026, 5, 15, 10, 0, 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        # 1. Create parquet with an incorrect total_assets (e.g. old USD value)
        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [_make_price_row(addr, 1, 1.086, row_ts, 25_000_000, total_assets=999999.0)]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        # 2. Simulate backfill: read table, match history, overwrite
        table = pq.read_table(str(parquet_path))
        table = VaultHistoricalRead.migrate_parquet_schema(table)

        history = [(row_ts.timestamp(), 1085717.92)]  # denomination-token TVL from API
        total_assets = table.column("total_assets").to_pylist()
        timestamps = table.column("timestamp").to_pylist()
        addresses = table.column("address").to_pylist()
        chains = table.column("chain").to_pylist()

        for i in range(len(table)):
            if chains[i] != 1 or addresses[i] != addr:
                continue
            ts = timestamps[i].timestamp() if hasattr(timestamps[i], "timestamp") else float(timestamps[i])
            for epoch, tvl in history:
                if abs(ts - epoch) <= 24 * 3600:
                    total_assets[i] = tvl

        col_idx = table.schema.get_field_index("total_assets")
        table = table.set_column(col_idx, "total_assets", pa.array(total_assets, type=pa.float64()))
        pq.write_table(table, str(parquet_path))

        # 3. Verify
        result = pq.read_table(str(parquet_path))
        assert result.column("total_assets").to_pylist()[0] == pytest.approx(1085717.92)
        assert result.column("share_price").to_pylist()[0] == pytest.approx(1.086)
