"""CSV file backed block data storage like block headers."""

import logging
from pathlib import Path
from typing import Tuple, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
from pandas import read_csv
from pyarrow.dataset import FilenamePartitioning

from .block_data_store import BlockDataStore


logger = logging.getLogger(__name__)


class NoGapsWritten(Exception):
    """Do not allow gaps in data."""


class CSVDatasetBlockDataStore(BlockDataStore):
    """Store block data as CSV file."""

    def __init__(
        self,
        path: Path,
    ):
        """

        :param path:
            Path to the CSV file

        """
        assert isinstance(path, Path)
        self.path = path
        assert self.path.suffix == ".csv"

    def is_virgin(self) -> bool:
        return not self.path.exists()

    def floor_block_number_to_partition_start(self, n) -> int:
        return n

    def load(self, since_block_number: int = 0) -> pd.DataFrame:
        """Load data from CSV file

        :param since_block_number:
            Ignored
        """
        assert since_block_number == 0, "Does not support incremental loading"
        df = read_csv(self.path)
        return df

    def save(self, df: pd.DataFrame, since_block_number: int = 0, check_contains_all_blocks=True):
        """Save all data to CSV file.

        :param since_block_number:
            Ignored. Does not support incremental writing.

        :param check_contains_all_blocks:
            Check that we have at least one data record for every block.
            Note that trades might not happen on every block.
        """

        assert since_block_number == 0, "Does not support incremental saving"

        # Make sure we do not miss blocks
        first_block = df.iloc[0]["block_number"]
        last_block = df.iloc[-1]["block_number"]

        # Try to assert we do not write out bad data
        if check_contains_all_blocks:
            series = df["block_number"]
            for i in range(first_block, last_block):
                if i not in series:
                    raise NoGapsWritten(f"Gap in block data detected. First block: {first_block:,}, last block: {last_block:,}, missing block: {i}")

        df.to_csv(self.path)

    def save_incremental(self, df: pd.DataFrame) -> Tuple[int, int]:
        """Write all partitions we are missing from the data."""
        raise NotImplementedError("Not supported for CSV")

    def peak_last_block(self) -> Optional[int]:
        """Return the last block number stored on the disk."""
        raise NotImplementedError("Not supported for CSV")
