"""Test external TVL stamping into parquet total_assets.

The post-scan stamp_external_tvl() writes the current API TVL into
total_assets on the latest existing row. Over successive scan cycles
this builds up a historical total_assets time series from offchain data.

1. Verify stamping updates only the latest row per (chain, address)
2. Verify share_price is preserved (row survives cleaning)
3. Verify (chain, address) keying prevents cross-chain contamination
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


def _make_mock_vault(address: str, tvl_usd: Decimal | None, chain_id: int = 1) -> MagicMock:
    """Create a mock vault with the required interface."""
    vault = MagicMock()
    vault.address = address
    vault.chain_id = chain_id
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
        "written_at": timestamp,
    }


def test_stamp_updates_latest_row():
    """Stamp external TVL onto total_assets of the latest row only.

    1. Create a parquet with two price rows at different timestamps
    2. Run stamp_external_tvl
    3. Verify only the latest row gets total_assets updated
    4. Verify row count unchanged (no new rows)
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

        # 2. Run stamp
        forge_vault = _make_mock_vault(addr, Decimal("1085984.11"))
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[forge_vault])

        # 3. Verify
        assert count == 1
        result = pq.read_table(str(parquet_path))
        assert len(result) == 2

        ta = result.column("total_assets").to_pylist()
        assert math.isnan(ta[0])
        assert ta[1] == pytest.approx(1085984.11)

        # 4. share_price preserved
        assert result.column("share_price").to_pylist()[1] == pytest.approx(1.06)


def test_stamp_keys_by_chain_and_address():
    """Same address on two chains does not cross-contaminate.

    1. Create parquet with same address on chain 1 and chain 42161
    2. Stamp for chain 1 only
    3. Verify chain 42161 row stays NaN
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

        forge_vault = _make_mock_vault(addr, Decimal("1000000.00"), chain_id=1)
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[forge_vault])

        assert count == 1
        result = pq.read_table(str(parquet_path))
        ta = result.column("total_assets").to_pylist()
        chains = result.column("chain").to_pylist()

        assert ta[chains.index(1)] == pytest.approx(1000000.00)
        assert math.isnan(ta[chains.index(42161)])


def test_stamp_skips_vaults_without_external_tvl():
    """Vaults that return None from fetch_tvl_usd are skipped.

    1. Create parquet with a row
    2. Run with a vault that returns None
    3. Verify 0 vaults stamped
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        schema = VaultHistoricalRead.to_pyarrow_schema()
        rows = [_make_price_row("0xaaaa", 1, 1.0, datetime.datetime(2026, 5, 28), 100)]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, str(parquet_path))

        vault = _make_mock_vault("0xaaaa", None)
        count = stamp_external_tvl(output_fname=parquet_path, vaults=[vault])

        assert count == 0
