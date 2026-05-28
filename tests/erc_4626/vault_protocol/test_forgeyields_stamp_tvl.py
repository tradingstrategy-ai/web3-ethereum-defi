"""Test ForgeYields external TVL stamping into parquet.

The post-scan stamp_external_tvl() function appends a point-in-time
TVL row for vaults where on-chain TVL is not available.

1. Create a minimal parquet with one empty row
2. Run stamp_external_tvl() with the ForgeYields vault
3. Verify the tvl_usd column is populated in the new row
"""

import datetime
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


def test_stamp_external_tvl():
    """Stamp external TVL into an existing parquet file.

    1. Create a minimal parquet file with the canonical schema
    2. Create two mock vaults: one with external TVL, one with on-chain TVL
    3. Run stamp_external_tvl
    4. Verify only the external vault got a new row with tvl_usd populated
    """
    now = datetime.datetime(2026, 5, 28, 12, 0, 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        # 1. Create minimal parquet
        schema = VaultHistoricalRead.to_pyarrow_schema()
        empty_table = pa.table(
            {field.name: pa.array([], type=field.type) for field in schema},
            schema=schema,
        )
        pq.write_table(empty_table, str(parquet_path))

        # 2. Create mock vaults
        forge_vault = _make_mock_vault(
            address="0x943109dc7c950da4592d85ebd4cfed007af64670",
            tvl_usd=Decimal("1085984.11"),
            historical_supported=False,
        )
        normal_vault = _make_mock_vault(
            address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            tvl_usd=None,
            historical_supported=True,
        )

        # 3. Run stamp_external_tvl
        count = stamp_external_tvl(
            output_fname=parquet_path,
            vaults=[forge_vault, normal_vault],
            now_=now,
        )

        # 4. Verify
        assert count == 1

        table = pq.read_table(str(parquet_path))
        assert len(table) == 1

        row = table.to_pydict()
        assert row["address"][0] == "0x943109dc7c950da4592d85ebd4cfed007af64670"
        assert row["tvl_usd"][0] == pytest.approx(1085984.11)
        assert row["chain"][0] == 1
        assert row["block_number"][0] == 0


def test_stamp_external_tvl_no_external_vaults():
    """Verify no rows are written when all vaults support on-chain TVL.

    1. Create a minimal parquet file
    2. Run stamp_external_tvl with only on-chain vaults
    3. Verify 0 rows stamped and file unchanged
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = Path(tmpdir) / "vault-prices-1h.parquet"

        # 1. Create minimal parquet
        schema = VaultHistoricalRead.to_pyarrow_schema()
        empty_table = pa.table(
            {field.name: pa.array([], type=field.type) for field in schema},
            schema=schema,
        )
        pq.write_table(empty_table, str(parquet_path))

        # 2. Run with only on-chain vaults
        normal_vault = _make_mock_vault(
            address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            tvl_usd=None,
            historical_supported=True,
        )
        count = stamp_external_tvl(
            output_fname=parquet_path,
            vaults=[normal_vault],
        )

        # 3. Verify no rows added
        assert count == 0
