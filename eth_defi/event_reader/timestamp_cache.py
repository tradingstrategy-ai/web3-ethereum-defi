"""DuckDB-based cache for block number -> timestamp mapping."""

import pandas as pd
import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path constant (assumed from context)
DEFAULT_TIMESTAMP_CACHE_FILE = Path.home() / ".tradingstrategy" / Path("block-timestamps.duckdb")


class BlockTimestampDatabase:
    """Mapping of chain ID -> block number -> timestamp using DuckDB.

    - Internal storage: DuckDB on-disk database (or in-memory).
    - Efficient selective loading and upserting.

    For usage see `eth_defi.event_reader.multicall_timestamp.fetch_block_timestamps_multiprocess_auto_backend`
    """

    def __init__(self, path: Path | str = DEFAULT_TIMESTAMP_CACHE_FILE):
        """Initialize the database connection.

        :param path: Path to the DuckDB file. Use ':memory:' for transient storage.
        """

        # Be lazy about this so we do not mess imports
        import duckdb

        self.path = str(path)
        self.con = duckdb.connect(self.path)
        self._init_schema()

    def _init_schema(self):
        """Ensure the table exists with the correct schema and primary key."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS block_timestamps (
                chain_id UINTEGER,
                block_number UINTEGER,
                timestamp TIMESTAMP,
                PRIMARY KEY (chain_id, block_number)
            )
        """)

    def import_chain_data(self, chain_id: int, data: dict[int, datetime.datetime]):
        """Import data from raw dictionary format to the database.

        Uses an upsert strategy (ON CONFLICT REPLACE) to ensure latest data is kept.

        :param chain_id: Chain ID for the data being imported.

        :param data: Mapping of block number (int) to timestamp (datetime).
        """
        if not data:
            return

        # 1. Convert dict to a temporary DataFrame for easy bulk insertion
        # Note: We use a DataFrame here as an intermediate transport buffer,
        # not as the persistent store.
        df_new = pd.DataFrame([{"chain_id": chain_id, "block_number": k, "timestamp": v} for k, v in data.items()])

        # 2. Register df as a view so DuckDB can query it
        self.con.register("df_view", df_new)

        # 3. Perform Insert / On Conflict Replace
        self.con.execute("""
            INSERT INTO block_timestamps (chain_id, block_number, timestamp)
            SELECT chain_id, block_number, timestamp FROM df_view
            ON CONFLICT (chain_id, block_number) DO UPDATE SET timestamp = EXCLUDED.timestamp
        """)

        # Cleanup view
        self.con.unregister("df_view")

    @staticmethod
    def load(path: Path, read_only: bool = False) -> "BlockTimestampDatabase":
        """Load the database from disk.

        :param read_only: If True, opens the connection in read-only mode (good for multiprocess readers).
        """
        db = BlockTimestampDatabase(path)
        if read_only:
            # Re-connect in read_only mode specifically
            db.con.close()
            db.con = duckdb.connect(str(path), read_only=True)
        return db

    @staticmethod
    def create(path: Path) -> "BlockTimestampDatabase":
        """Create an in-memory instance."""
        return BlockTimestampDatabase(path)

    def save(self):
        """Force a checkpoint.

        Note: DuckDB usually auto-commits. If moving from :memory: to disk,
        we need to copy.
        """

        # Just ensure WAL is flushed
        self.con.commit()

    def get_first_and_last_block(self, chain_id: int) -> tuple[int, int]:
        """Get the first and last block numbers we have for a given chain ID.

        :return: 0,0 if no data
        """
        res = self.con.execute(
            """
            SELECT MIN(block_number), MAX(block_number) 
            FROM block_timestamps 
            WHERE chain_id = ?
        """,
            [chain_id],
        ).fetchone()

        if res is None or res[0] is None:
            return 0, 0
        return res[0], res[1]

    def __getitem__(self, chain_id: int) -> pd.Series | None:
        """Get timestamps for a single chain.

        Returns a Pandas Series to maintain compatibility with the original API.

        :return: Pandas series block number (int) -> block timestamp (pd.Timestamp)
        """
        # Selectively load only the specific chain ID
        df = self.con.execute(
            """
            SELECT block_number, timestamp 
            FROM block_timestamps 
            WHERE chain_id = ? 
            ORDER BY block_number ASC
        """,
            [chain_id],
        ).df()

        if df.empty:
            return None

        # Set index to match original behavior
        df.set_index("block_number", inplace=True)
        return df["timestamp"]

    def close(self):
        """Close the connection."""
        self.con.close()


def load_timestamp_cache(cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FILE) -> BlockTimestampDatabase:
    logger.info(f"Loading block timestamps from {cache_file}")
    return BlockTimestampDatabase.load(cache_file)


def save_timestamp_cache(timestamps: BlockTimestampDatabase, cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FILE):
    assert isinstance(timestamps, BlockTimestampDatabase), f"Expected BlockTimestampDatabase, got {type(timestamps)}"

    # In DuckDB, data is persisted immediately on insert/update if connected to a file.
    # We call save() to ensure WAL is flushed or if we need to export from memory.
    timestamps.save()

    if cache_file.exists():
        size_mb = cache_file.stat().st_size / (1024 * 1024)
        logger.info(f"Ensured block timestamps saved to {cache_file}, size is {size_mb:.2f} MB")
