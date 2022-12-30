"""Storing block headeres in Parquet testing."""

import os
import tempfile
from pathlib import Path


from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.event_reader.csv_block_data_store import CSVDatasetBlockDataStore


def test_write_store():
    """Write block headers to sharded Parquet dataset."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)

    df = BlockHeader.to_pandas(headers)

    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir).joinpath("test.csv")
        store = CSVDatasetBlockDataStore(path)
        store.save(df)
        assert os.path.exists(path)


def test_read_store():
    """Read data back from sharded Parquet dataset."""

    # Generate 25k blocks
    headers = BlockHeader.generate_headers(25_000)
    assert headers["timestamp"][0] == 0
    first_block_hash = headers["block_hash"][0]
    df = BlockHeader.to_pandas(headers, partition_size=10_000)
    assert len(df) == 25_000

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir).joinpath("test.csv")
        store = CSVDatasetBlockDataStore(path)
        store.save(df)
        df = store.load()

    assert len(df) == 25_000
    first_block = df.loc[df.block_number == 1].iloc[0]
    assert first_block.block_number == 1
    assert first_block.block_hash == first_block_hash
    assert first_block.timestamp == 0

    second_block = df.loc[df.block_number == 2].iloc[0]
    assert second_block.block_number == 2
    assert second_block.timestamp == 12
