"""Parquet dataset backed block header storage."""

from pathlib import Path
from typing import Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
from pyarrow._dataset import FilenamePartitioning

from .block_header_store import BlockHeaderStore


class ParquetDatasetBlockHeaderStore(BlockHeaderStore):
    """Store block headers as Parquet dataset.

    - Partitions are keyed by block number

    - Partitioning allows fast incremental updates,
      by overwriting the last two partitions
    """

    def __init__(self,
                 path: Path,
                 partition_size=100_000,
                 ):
        """

        :param path:
            Directory and a metadata file there

        :param partition_size:
        """
        assert isinstance(path, Path)
        self.path = path
        self.partition_size = partition_size

        part_scheme = FilenamePartitioning(
            pa.schema([("partition", pa.uint32())])
        )

        self.partitioning = part_scheme

    def floor_block_number_to_partition(self, n) -> int:
        return n // self.partition_size * self.partition_size

    def load(self, since_block_number: int = 0) -> pd.DataFrame:
        """Load data from parquet.

        :param since_block_number:
            May return earlier rows than this if a block is a middle of a partition
        """
        #dataset = ds.parquet_dataset(self.path, partitioning=self.partitioning)
        dataset = ds.dataset(self.path, partitioning=self.partitioning)
        partition_start_block = self.floor_block_number_to_partition(since_block_number)
        return dataset.to_table(filter=ds.field('partition') >= partition_start_block).to_pandas()

    def save(self, df: pd.DataFrame, since_block_number: int = 0):
        """Savea all data from parquet.

        If there are existing block headers written, any data will be overwritten
        on per partition basis.

        :param since_block_number:
            Write only the latest data after this block number (inclusive)
        """

        assert "partition" in df.columns

        if since_block_number:
            df = df.loc[df.block_number >= since_block_number]

        table = pa.Table.from_pandas(df)
        ds.write_dataset(table,
                         self.path,
                         format="parquet",
                         partitioning=self.partitioning,
                         existing_data_behavior="delete_matching",
                         )

    def save_incremental(self, df: pd.DataFrame) -> Tuple[int, int]:
        """Write all partitions we are missing from the data.


        """

        last_written_block = self.peak_last_block()
        last_block_number = df.iloc[-1]["block_number"]
        first_block_needs_to_written = self.floor_block_number_to_partition(last_block_number) - self.partition_size
        first_block_needs_to_written = max(1, first_block_needs_to_written)
        self.save(df, first_block_needs_to_written)
        return first_block_needs_to_written, last_block_number

    def peak_last_block(self) -> int:
        """Return the last block number stored on the disk."""
        dataset = ds.dataset(self.path, partitioning=self.partitioning)
        fragments = list(dataset.get_fragments())
        last_fragment = fragments[-1]
        # TODO: How to select last row with pyarrow
        df = last_fragment.to_table().to_pandas()
        return df.iloc[-1]["block_number"]



