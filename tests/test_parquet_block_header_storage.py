"""Storing block headeres in Parquet testing."""

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.event_reader.parquet_block_data_store import ParquetDatasetBlockDataStore, NoGapsWritten

try:
    import pyarrow

    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

pytestmark = pytest.mark.skipif(
    HAS_PYARROW == False,
    reason="Need Pyarrow support to run these tests",
)


def test_write_store():
    """Write block headers to sharded Parquet dataset."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)

    df = BlockHeader.to_pandas(headers, partition_size=10_000)

    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)
        store = ParquetDatasetBlockDataStore(path)
        store.save(df)
        assert os.path.exists(Path(tmp_dir, "1_part-0.parquet"))
        assert os.path.exists(Path(tmp_dir, "10000_part-0.parquet"))
        assert os.path.exists(Path(tmp_dir, "20000_part-0.parquet"))


def test_read_store():
    """Read data back from sharded Parquet dataset."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)
    assert headers["timestamp"][0] == 0
    first_block_hash = headers["block_hash"][0]
    df = BlockHeader.to_pandas(headers, partition_size=10_000)
    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)
        store = ParquetDatasetBlockDataStore(path)
        store.save(df)

        assert store.peak_last_block() == 25_000
        df = store.load()

    assert len(df) == 25_000
    first_block = df.loc[df.block_number == 1].iloc[0]
    assert first_block.block_number == 1
    assert first_block.block_hash == first_block_hash
    assert first_block.timestamp == 0

    second_block = df.loc[df.block_number == 2].iloc[0]
    assert second_block.block_number == 2
    assert second_block.timestamp == 12


def test_read_partial():
    """Read only last N blocks."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)
    assert headers["timestamp"][0] == 0
    partion_size = 10_000
    df = BlockHeader.to_pandas(headers, partition_size=partion_size)
    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)
        store = ParquetDatasetBlockDataStore(path, partition_size=partion_size)
        store.save(df)

        # Load blocks 10,000 - 25,000
        df = store.load(10_000)

        assert df.iloc[0].block_number == 10_000
        assert df.iloc[1].block_number == 10_001
        assert len(df) == 15001

        # Load blocks 24,000 - 25,000,
        # Matches partition 20,000 - 25,000
        df = store.load(24000)
        assert df.iloc[0].block_number == 20_000
        assert df.iloc[1].block_number == 20_001
        assert df.iloc[-11].block_number == 24_990
        assert len(df) == 5001


def test_peak_block():
    """Peak the last written block."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)
    assert headers["timestamp"][0] == 0

    df = BlockHeader.to_pandas(headers, partition_size=10_000)
    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)
        store = ParquetDatasetBlockDataStore(path)
        store.save(df)
        assert store.peak_last_block() == 25_000


def test_write_incremental():
    """Write data in batchds."""

    # Generate 25k blocks
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)

        partition_size = 10_000

        store = ParquetDatasetBlockDataStore(path, partition_size=partition_size)

        headers = BlockHeader.generate_headers(1000)
        df = BlockHeader.to_pandas(headers, partition_size=partition_size)
        store.save_incremental(df)
        assert store.peak_last_block() == 1000

        headers = BlockHeader.generate_headers(1000, start_block=headers["block_number"][-1] + 1, start_time=headers["timestamp"][-1] + 12)

        df2 = BlockHeader.to_pandas(headers, partition_size=partition_size)
        df = pd.concat([df, df2])
        written_first, written_last = store.save_incremental(df)
        assert written_first == 1
        assert written_last == 2000
        assert store.peak_last_block() == 2000

        # See we got all blocks
        check_df = store.load()
        assert len(check_df) == 2000

        # Fill few partitions
        headers = BlockHeader.generate_headers(30_000, start_block=headers["block_number"][-1] + 1, start_time=headers["timestamp"][-1] + 12)
        df3 = BlockHeader.to_pandas(headers, partition_size=partition_size)
        df = pd.concat([df, df3])
        written_first, written_last = store.save_incremental(df)
        assert written_first == 1
        assert written_last == 32_000
        assert store.peak_last_block() == 32_000

        # See we got all blocks
        check_df = store.load()
        assert len(check_df) == 32_000

        # Fill few more partitions
        headers = BlockHeader.generate_headers(30_000, start_block=headers["block_number"][-1] + 1, start_time=headers["timestamp"][-1] + 12)
        df4 = BlockHeader.to_pandas(headers, partition_size=partition_size)
        df = pd.concat([df, df4])
        written_first, written_last = store.save_incremental(df)
        assert written_first == 30_000
        assert written_last == 62_000
        assert store.peak_last_block() == 62_000

        # See we got all blocks
        check_df = store.load()
        assert len(check_df) == 62_000

        # Fill same of the current partition
        headers = BlockHeader.generate_headers(1000, start_block=headers["block_number"][-1] + 1, start_time=headers["timestamp"][-1] + 12)
        df4 = BlockHeader.to_pandas(headers, partition_size=partition_size)
        df = pd.concat([df, df4])
        written_first, written_last = store.save_incremental(df)
        assert written_first == 50_000
        assert written_last == 63_000
        assert store.peak_last_block() == 63_000

        # See we got all blocks
        check_df = store.load()
        assert len(check_df) == 63_000


def test_write_no_gaps():
    """Do not allow gaps in data."""

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir)

        partition_size = 10_000

        store = ParquetDatasetBlockDataStore(path, partition_size=partition_size)

        headers = BlockHeader.generate_headers(25000)
        df = BlockHeader.to_pandas(headers, partition_size=partition_size)
        store.save_incremental(df)
        assert store.peak_last_block() == 25000

        headers = BlockHeader.generate_headers(1000, start_block=headers["block_number"][-1] + 100, start_time=headers["timestamp"][-1] + 12)

        df2 = BlockHeader.to_pandas(headers, partition_size=partition_size)
        df = pd.concat([df, df2])
        with pytest.raises(NoGapsWritten):
            store.save_incremental(df)
