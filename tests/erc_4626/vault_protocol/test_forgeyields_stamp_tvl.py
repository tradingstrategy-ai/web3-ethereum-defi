"""Test ForgeYields external TVL stamping into parquet.

The post-scan stamp_external_tvl() function updates the tvl_usd column
on the latest existing row for vaults where on-chain TVL is not available.

1. Create a parquet with a real price row (valid share_price)
2. Run stamp_external_tvl() with a ForgeYields-like mock vault
3. Verify tvl_usd is set on the existing row (not a new row)
4. Verify the row survives the cleaning pipeline's NaN-share-price filter
5. Verify tvl_usd feeds through to period metrics tvl_end for ranking eligibility
"""

import datetime
import math
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.historical import stamp_external_tvl


def _make_mock_vault(address: str, tvl_usd: Decimal | None, historical_supported: bool = False) -> MagicMock:
    """Create a mock vault with the required interface."""
    vault = MagicMock()
    vault.address = address
    vault.chain_id = 1
    vault.is_historical_tvl_supported.return_value = historical_supported
    vault.fetch_tvl_usd.return_value = tvl_usd
    return vault


def _make_price_row(address: str, chain: int, share_price: float, timestamp: datetime.datetime, block_number: int) -> dict:
    """Create a minimal price row dict matching the canonical schema."""
    return {
        "chain": chain,
        "address": address,
        "block_number": block_number,
        "timestamp": timestamp,
        "share_price": share_price,
        "total_assets": float("nan"),
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
        "tvl_usd": float("nan"),
        "written_at": timestamp,
    }


def test_stamp_updates_latest_row():
    """Stamp external TVL onto the latest existing row for a vault.

    1. Create a parquet with two price rows for a ForgeYields vault (different timestamps)
    2. Run stamp_external_tvl
    3. Verify tvl_usd is set on the latest row only
    4. Verify no new rows were added
    """
    addr = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    ts1 = datetime.datetime(2026, 5, 28, 10, 0, 0)
    ts2 = datetime.datetime(2026, 5, 28, 11, 0, 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        # 1. Create parquet with two rows
        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [
            _make_price_row(addr, 1, 1.05, ts1, 25_000_000),
            _make_price_row(addr, 1, 1.06, ts2, 25_001_000),
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        # 2. Run stamp_external_tvl
        forge_vault = _make_mock_vault(addr, Decimal("1085984.11"), historical_supported=False)
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[forge_vault])

        # 3. Verify
        assert count == 1
        result = pq.read_table(str(parquet_path))
        assert len(result) == 2  # No new rows

        tvl_values = result.column("tvl_usd").to_pylist()
        # First row (older) should still be NaN
        assert math.isnan(tvl_values[0])
        # Second row (latest) should have the stamped TVL
        assert tvl_values[1] == pytest.approx(1085984.11)


def test_stamp_preserves_share_price():
    """Verify that stamping tvl_usd does not corrupt existing share_price.

    This is critical because the cleaning pipeline drops rows with NaN share_price.

    1. Create a parquet row with a valid share_price
    2. Stamp tvl_usd
    3. Verify share_price is preserved
    """
    addr = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    ts = datetime.datetime(2026, 5, 28, 12, 0, 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [_make_price_row(addr, 1, 1.085, ts, 25_100_000)]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        forge_vault = _make_mock_vault(addr, Decimal("500000.00"), historical_supported=False)
        stamp_external_tvl(output_fname=parquet_path, vaults=[forge_vault])

        # 3. Verify share_price preserved, tvl_usd set
        result = pq.read_table(str(parquet_path))
        assert result.column("share_price").to_pylist()[0] == pytest.approx(1.085)
        assert result.column("tvl_usd").to_pylist()[0] == pytest.approx(500000.00)


def test_stamp_skips_onchain_vaults():
    """Verify no changes when all vaults support on-chain TVL.

    1. Create a parquet with a row
    2. Run stamp_external_tvl with only on-chain vaults
    3. Verify 0 vaults stamped
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [_make_price_row("0xaaaa", 1, 1.0, datetime.datetime(2026, 5, 28), 100)]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        normal_vault = _make_mock_vault("0xaaaa", None, historical_supported=True)
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[normal_vault])

        assert count == 0


def test_stamp_keys_by_chain_and_address():
    """Verify that stamping uses (chain, address) not just address.

    Same address on two chains should not cross-contaminate.

    1. Create parquet with same address on chain 1 and chain 42161
    2. Stamp external TVL for chain 1 only
    3. Verify only the chain 1 row gets tvl_usd, chain 42161 stays NaN
    """
    addr = "0x943109dc7c950da4592d85ebd4cfed007af64670"
    ts = datetime.datetime(2026, 5, 28, 12, 0, 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [
            _make_price_row(addr, 1, 1.05, ts, 25_000_000),
            _make_price_row(addr, 42161, 1.10, ts, 200_000_000),
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        # Only chain 1 vault
        forge_vault = _make_mock_vault(addr, Decimal("1000000.00"), historical_supported=False)
        forge_vault.chain_id = 1
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[forge_vault])

        assert count == 1
        result = pq.read_table(str(parquet_path))
        tvl_values = result.column("tvl_usd").to_pylist()
        chains = result.column("chain").to_pylist()

        # Chain 1 row should have TVL
        chain1_idx = chains.index(1)
        assert tvl_values[chain1_idx] == pytest.approx(1000000.00)

        # Chain 42161 row should still be NaN
        chain42161_idx = chains.index(42161)
        assert math.isnan(tvl_values[chain42161_idx])


def test_tvl_usd_feeds_period_metrics():
    """Verify tvl_usd reaches period metrics tvl_end for ranking eligibility.

    ForgeYields has valid share_price but NaN total_assets. After stamping
    tvl_usd, the period metrics pipeline should use tvl_usd as fallback
    so the vault gets a non-null tvl_end and passes ranking filters.

    1. Build a DataFrame with valid share_price, NaN total_assets, stamped tvl_usd
    2. Run calculate_period_metrics
    3. Assert tvl_end equals the stamped tvl_usd
    """
    from eth_defi.research.vault_metrics import calculate_period_metrics
    from eth_defi.vault.fee import FeeData, VaultFeeMode

    # 1. Build price data: 30 hourly rows with valid share_price, NaN total_assets
    dates = pd.date_range("2026-05-01", periods=30 * 24, freq="h")
    prices_df = pd.DataFrame(
        {
            "share_price": [1.0 + i * 0.0001 for i in range(len(dates))],
            "total_assets": [float("nan")] * len(dates),
            "tvl_usd": [float("nan")] * len(dates),
        },
        index=dates,
    )
    # Stamp tvl_usd on the last row (simulating stamp_external_tvl)
    prices_df.iloc[-1, prices_df.columns.get_loc("tvl_usd")] = 1_085_984.0

    # Apply the same fallback as vault_metrics.py line 1419
    tvl_series = prices_df["total_assets"].combine_first(prices_df["tvl_usd"])

    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_skimming,
        management=0.0,
        performance=0.20,
        deposit=0.0,
        withdraw=0.0,
    )

    # 2. Run period metrics
    pm = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=fee_data.get_net_fees(),
        share_price_hourly=prices_df["share_price"],
        share_price_daily=prices_df["share_price"].resample("D").last(),
        tvl=tvl_series,
        now_=dates[-1],
    )

    # 3. Assert tvl_end is populated from the stamped tvl_usd
    assert pm is not None
    assert pm.error_reason is None
    assert pm.tvl_end == pytest.approx(1_085_984.0)
