import datetime
from pathlib import Path
import logging


import pandas as pd


#: Where we store our block header timestamps cache by default
DEFAULT_TIMESTAMP_CACHE_FILE = Path.home() / ".cache" / "tradingstrategy" / "block-timestamps.parquet"


logger = logging.getLogger(__name__)


class BlockTimestampDatabase:
    """Mapping of chain ID -> block number -> timestamp.

    - Internal presentation as Pandas series to save memory and disk space
    - Use more efficient Parquet save/load format
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def import_chain_data(self, chain_id: int, data: dict[int, datetime.datetime]):
        """Import data from raw dictionary format to a chain slice."""
        s = pd.Series(data, dtype="datetime64[s]")

        # Create a matching MultiIndex: (chain_id, block_number)
        mi = pd.MultiIndex.from_product(
            [[chain_id], s.index],
            names=["chain_id", "block_number"],
        )
        s = pd.Series(s.values, index=mi, name="timestamp")

        self.df = pd.concat([self.df, s.to_frame()]).sort_index()
        self.df = self.df[~self.df.index.duplicated(keep="last")]  # keep latest

    @staticmethod
    def load(path: Path) -> "ChainBlockTimestampMap":
        df = pd.read_parquet(path)
        return BlockTimestampDatabase(df)

    @staticmethod
    def create() -> "ChainBlockTimestampMap":
        df = BlockTimestampDatabase.create_dataframe()
        return BlockTimestampDatabase(df)

    def save(self, path: Path):
        self.df.to_parquet(path, index=True)

    @staticmethod
    def create_dataframe() -> pd.DataFrame:
        # Define the MultiIndex levels and names
        levels = [[], []]  # initially empty
        codes = [[], []]  # initially empty
        names = ["chain_id", "block_number"]

        multi_index = pd.MultiIndex(levels=levels, codes=codes, names=names)

        # Create empty DataFrame with the correct column and dtype
        df = pd.DataFrame(
            {"timestamp": pd.Series(dtype="datetime64[s]")},  # 1-second precision
            index=multi_index,
        )
        return df

    def get_first_and_last_block(self, chain_id: int) -> tuple[int, int]:
        """Get the first and last block numbers we have for a given chain ID.

        :return:
            0,0 if no data
        """
        chain_blocks = self[chain_id]
        if chain_blocks is None or len(chain_blocks) == 0:
            return 0, 0
        return chain_blocks.index[0], chain_blocks.index[-1]

    def __getitem__(self, chain_id) -> pd.Series | None:
        """Get timestamps for a single chain

        :return:
            Pandas series block number (int) -> block timestamp (pd.Timestamp)
        """
        try:
            chain_df = self.df.xs(chain_id, level="chain_id")  # keeps 'block_number' as the index
        except KeyError:
            return None
        return chain_df["timestamp"]


def load_timestamp_cache(cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FILE) -> BlockTimestampDatabase:
    logger.info(f"Loading block timestamps from {cache_file}")
    return BlockTimestampDatabase.load(cache_file)


def save_timestamp_cache(timestamps: BlockTimestampDatabase, cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FILE):
    assert isinstance(timestamps, BlockTimestampDatabase), f"Expected BlockTimestampDatabase, got {type(timestamps)}"
    timestamps.save(cache_file)
    size = cache_file.stat().st_size
    logger.info(f"Saved block timestamps to {cache_file}, size is {size / 1024 * 1024} MB")
