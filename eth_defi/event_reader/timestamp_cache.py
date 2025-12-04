"""DuckDB-based cache for block number -> timestamp mapping.

By default, we manage a database file at ~/.tradingstrategy/block-timestamps.duckdb` where we have chain -> block -> timestamp mapping.
Getting block numbers and timestamps is a common expensive operation when scanning historical events.
"""

import pandas as pd
import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path constant (assumed from context)
DEFAULT_TIMESTAMP_CACHE_FOLDER = Path.home() / ".tradingstrategy" / "block-timestamp"


class BlockTimestampDatabase:
    """Mapping of chain ID -> block number -> timestamp using DuckDB.

    - Internal storage: DuckDB on-disk database (or in-memory).
    - Efficient selective loading and upserting
    - One second precision for disk space and speed savings

    For usage see `eth_defi.event_reader.multicall_timestamp.fetch_block_timestamps_multiprocess_auto_backend`
    """

    def __init__(
        self,
        chain_id: int,
        path: Path,
    ):
        """Initialize the database connection.

        :param path: Path to the DuckDB file. Use ':memory:' for transient storage.
        """

        assert type(chain_id) is int, f"Expected int chain_id, got {type(chain_id)}"
        assert isinstance(path, Path), f"Expected str or Path for path, got {type(path)}"

        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        # Create cache folder if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Be lazy about this so we do not mess imports
        import duckdb

        self.chain_id = chain_id
        self.path = path
        self.con = duckdb.connect(self.path)
        self._init_schema()

    def _init_schema(self):
        """Ensure the table exists with the correct schema and primary key.

        - Disk/speed optimised, because we are mostly using this for vault events
        - We have plenty of time before year 2038, and I won't be around
        """
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS block_timestamps (
                block_number UINT64 PRIMARY KEY,
                timestamp UINT32,
            )
        """)

    def import_chain_data(self, chain_id: int, data: dict[int, datetime.datetime] | pd.Series):
        """Import data from raw dictionary format to the database.

        - Uses an upsert strategy (ON CONFLICT REPLACE) to ensure latest data is kept.

        :param chain_id: Chain ID for the data being imported.

        :param data:
            Mapping of block number (int) to timestamp (datetime).

            Give block number -> unix timestamp pd.Series for max speed.
        """

        assert chain_id == self.chain_id, f"Import chain_id {chain_id} does not match database chain_id {self.chain_id}"

        # 1. Convert dict to a temporary DataFrame for easy bulk insertion
        # Note: We use a DataFrame here as an intermediate transport buffer,
        # not as the persistent store.
        if isinstance(data, pd.Series):
            df_new = pd.DataFrame(
                {
                    "block_number": data.index,
                    "timestamp": data.values,
                }
            )
            df_new["timestamp"] = df_new["timestamp"].astype("uint32")
        else:
            # Legacy path
            df_new = pd.DataFrame([{"block_number": k, "timestamp": v} for k, v in data.items()])
            # Convert to 32-bit unix timestamp
            df_new["timestamp"] = (df_new["timestamp"].astype("int64") // 10**9).astype("uint32")

        # 2. Register df as a view so DuckDB can query it
        self.con.register("df_view", df_new)

        # 3. Perform Insert / On Conflict Replace
        self.con.execute("""
            INSERT INTO block_timestamps (block_number, timestamp)
            SELECT block_number, timestamp FROM df_view
            ON CONFLICT (block_number) DO UPDATE SET timestamp = EXCLUDED.timestamp
        """)

        # Cleanup view
        self.con.unregister("df_view")

    @staticmethod
    def get_database_file_chain(chain_id: int, path=DEFAULT_TIMESTAMP_CACHE_FOLDER) -> Path:
        """Get the default database file path for a given chain ID."""
        if path.exists():
            assert path.is_dir(), f"Expected directory path, got {path}"
        return path / f"{chain_id}-timestamps.duckdb"

    @staticmethod
    def load(chain_id: int, path: Path) -> "BlockTimestampDatabase":
        """Load the database from disk."""
        db = BlockTimestampDatabase(chain_id, path)
        return db

    @staticmethod
    def create(chain_id: int, path: Path) -> "BlockTimestampDatabase":
        """Create an in-memory instance."""
        file = BlockTimestampDatabase.get_database_file_chain(chain_id, path)
        return BlockTimestampDatabase(chain_id, file)

    def save(self):
        """Force a checkpoint.

        Note: DuckDB usually auto-commits. If moving from :memory: to disk,
        we need to copy.
        """

        # Just ensure WAL is flushed
        self.con.commit()

    def get_first_and_last_block(self) -> tuple[int, int]:
        """Get the first and last block numbers we have for a given chain ID.

        :return: 0,0 if no data
        """
        res = self.con.execute(
            """
            SELECT MIN(block_number), MAX(block_number) 
            FROM block_timestamps 
        """
        ).fetchone()

        if res is None or res[0] is None:
            return 0, 0
        return res[0], res[1]

    def to_series(self) -> pd.Series | None:
        """Get timestamps for a single chain.

        Returns a Pandas Series to maintain compatibility with the original API.

        :return: Pandas series block number (int) -> block timestamp (pd.Timestamp)
        """

        # Selectively load only the specific chain ID
        # We also need ORDER or
        df = self.con.execute(
            """
            SELECT block_number, timestamp 
            FROM block_timestamps 
            ORDER BY block_number ASC
        """
        ).df()

        if df.empty:
            return None

        # Set index to match original behavior
        df.set_index("block_number", inplace=True)
        return self.transform_time_values(df["timestamp"])

    def query(self, start_block: int, end_block: int) -> pd.Series | None:
        """Get timestamps for a single chain in an inclusive block range.

        Returns a Pandas Series to maintain compatibility with the original API.

        :param chain_id: EVM chain id
        :param start_block: Inclusive start block
        :param end_block: Inclusive end block
        :return: Pandas series block number (int) -> block timestamp (pd.Timestamp), or None if empty
        """
        if start_block >= end_block:
            raise ValueError("start_block must be <= end_block")

        df = self.con.execute(
            """
            SELECT block_number, timestamp
            FROM block_timestamps
            WHERE block_number BETWEEN ? AND ?
            ORDER BY block_number ASC
            """,
            [start_block, end_block],
        ).df()

        if df.empty:
            return None

        df.set_index("block_number", inplace=True)
        return self.transform_time_values(df["timestamp"])

    def transform_time_values(self, series: pd.Series) -> pd.Series:
        """Post-process our raw values from the database to actual time format.}

        :param series: Pandas Series with datetime values
        :return: Pandas Series with integer unix timestamps (seconds)
        """
        return pd.to_datetime(series, unit="s").astype("datetime64[s]")

    def close(self):
        """Close the connection."""
        self.con.close()


def load_timestamp_cache(chain_id: int, cache_folder: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER) -> BlockTimestampDatabase:
    cache_file = BlockTimestampDatabase.get_database_file_chain(chain_id, cache_folder)
    logger.info(f"Loading block timestamps from {cache_file}")
    return BlockTimestampDatabase.load(chain_id, cache_file)


def save_timestamp_cache(timestamps: BlockTimestampDatabase, cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER):
    assert isinstance(timestamps, BlockTimestampDatabase), f"Expected BlockTimestampDatabase, got {type(timestamps)}"

    # In DuckDB, data is persisted immediately on insert/update if connected to a file.
    # We call save() to ensure WAL is flushed or if we need to export from memory.
    timestamps.save()

    if cache_file.exists():
        size_mb = cache_file.stat().st_size / (1024 * 1024)
        logger.info(f"Ensured block timestamps saved to {cache_file}, size is {size_mb:.2f} MB")
