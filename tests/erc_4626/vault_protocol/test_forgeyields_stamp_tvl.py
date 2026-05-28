"""Test ForgeYields external TVL stamping into parquet.

The post-scan stamp_external_tvl() function updates the tvl_usd column
on the latest existing row for vaults where on-chain TVL is not available.

1. Create a parquet with a real price row (valid share_price)
2. Run stamp_external_tvl() with a ForgeYields-like mock vault
3. Verify tvl_usd is set on the existing row (not a new row)
4. Verify the row survives the cleaning pipeline's NaN-share-price filter
"""

import datetime
import math
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

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
