"""DuckDB-based cache for block number -> timestamp mapping.

Each chain has its own database at
``~/.tradingstrategy/block-timestamp/{chain_id}-timestamps.duckdb``.
Getting block numbers and timestamps is a common expensive operation when
scanning historical events.
"""

import datetime
import logging
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

try:
    import duckdb
except ImportError:
    # DuckDB is an optional extra; core vault imports must remain usable.
    duckdb = None

logger = logging.getLogger(__name__)

# Default path constant (assumed from context)
DEFAULT_TIMESTAMP_CACHE_FOLDER = Path.home() / ".tradingstrategy" / "block-timestamp"


class BlockTimestampDatabase:
    """Mapping of chain ID -> block number -> timestamp using DuckDB.

    - Internal storage: DuckDB on-disk database (or in-memory).
    - Efficient selective loading and upserting
    - One second precision for disk space and speed savings

    Modern high-throughput chains, including Monad, can produce multiple
    blocks during one Unix-timestamp second. Block timestamps are therefore a
    one-to-many mapping from timestamp to block number: equal timestamp values
    are expected and must not be used as unique block or observation IDs. One
    second precision is sufficient for this project's historical scans, so we
    deliberately preserve the shared timestamp instead of inventing
    higher-resolution values.

    For usage see `eth_defi.event_reader.multicall_timestamp.fetch_block_timestamps_multiprocess_auto_backend`
    """

    def __init__(
        self,
        chain_id: int,
        path: Path,
    ) -> None:
        """Initialise the database connection.

        DuckDB is needed only when opening a timestamp cache, not when
        importing the ordinary vault and Multicall interfaces.

        :param chain_id: Chain whose timestamps are stored in this file.
        :param path: Path to the DuckDB file. Use ':memory:' for transient storage.
        :return: ``None`` after the cache schema is ready.
        """
        self.con = None
        if duckdb is None:
            message = "Timestamp caches require the optional duckdb extra: pip install web3-ethereum-defi[duckdb]"
            raise ImportError(message)

        assert type(chain_id) is int, f"Expected int chain_id, got {type(chain_id)}"
        assert isinstance(path, Path), f"Expected str or Path for path, got {type(path)}"

        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        # Create cache folder if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        self.chain_id = chain_id
        self.path = path
        self.con = duckdb.connect(self.path)
        self._init_schema()

    def __del__(self) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None

    def _init_schema(self) -> None:
        """Create the timestamp table and migrate legacy ART constraints.

        Large file-backed ART indexes can corrupt the native heap with DuckDB
        1.5.0 on Python 3.14. Rebuild a legacy constrained table transactionally,
        preserving every row, before accepting more timestamp chunks.
        See https://github.com/duckdb/duckdb/issues/18190.

        :return: ``None`` after the unconstrained schema is ready.
        """
        self.con.execute("SET wal_autocheckpoint = '1TB'")
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS block_timestamps (
                block_number UINT64,
                timestamp UINT32
            )
        """)
        constraints = self.con.execute("""
            SELECT constraint_type FROM duckdb_constraints()
            WHERE table_name = 'block_timestamps'
              AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        """).fetchall()
        if constraints:
            row_count = self.get_count()
            logger.info("Migrating %s timestamp rows away from ART constraints in %s", f"{row_count:,}", self.path)
            self.con.execute("BEGIN TRANSACTION")
            try:
                self.con.execute("CREATE TABLE block_timestamps_migrated (block_number UINT64, timestamp UINT32)")
                self.con.execute("INSERT INTO block_timestamps_migrated SELECT block_number, timestamp FROM block_timestamps")
                migrated_count = self.con.execute("SELECT COUNT(*) FROM block_timestamps_migrated").fetchone()[0]
                assert migrated_count == row_count, f"Timestamp migration changed row count: {row_count} -> {migrated_count}"
                self.con.execute("DROP TABLE block_timestamps")
                self.con.execute("ALTER TABLE block_timestamps_migrated RENAME TO block_timestamps")
                self.con.execute("COMMIT")
            except (duckdb.Error, AssertionError):
                self.con.execute("ROLLBACK")
                raise
            logger.info("Migrated all %s timestamp rows in %s", f"{row_count:,}", self.path)

    def import_chain_data(self, chain_id: int, data: dict[int, datetime.datetime] | pd.Series) -> None:
        """Import data from raw dictionary format to the database.

        Stage deduplicated input, update changed timestamps through a hash join,
        then insert missing block numbers through an anti-join. Repeated chunks
        retain one row per block without requiring an ART-backed unique index.

        :param chain_id: Chain ID for the data being imported.

        :param data:
            Mapping of block number (int) to timestamp (datetime).

            Give block number -> unix timestamp pd.Series for max speed.

        :return: ``None`` after the chunk is committed.
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
            assert len(data) > 0, f"No data to import: {data}"
            # Legacy path
            df_new = pd.DataFrame([{"block_number": k, "timestamp": v} for k, v in data.items()])
            # Convert to 32-bit unix timestamp
            df_new["timestamp"] = (df_new["timestamp"].astype("datetime64[ns]").astype("int64") // 10**9).astype("uint32")

        if df_new.empty:
            return
        df_new = df_new.drop_duplicates(subset="block_number", keep="last")
        bounds = [int(df_new["block_number"].min()), int(df_new["block_number"].max())]
        self.con.register("df_view", df_new)
        self.con.execute("BEGIN TRANSACTION")
        try:
            self.con.execute(
                """
                UPDATE block_timestamps AS cached SET timestamp = incoming.timestamp
                FROM df_view AS incoming
                WHERE cached.block_number = incoming.block_number
                  AND cached.block_number BETWEEN ? AND ?
                  AND cached.timestamp IS DISTINCT FROM incoming.timestamp
                """,
                bounds,
            )
            self.con.execute(
                """
                INSERT INTO block_timestamps
                SELECT incoming.block_number, incoming.timestamp
                FROM df_view AS incoming
                ANTI JOIN (
                    SELECT block_number FROM block_timestamps
                    WHERE block_number BETWEEN ? AND ?
                ) AS cached ON cached.block_number = incoming.block_number
                """,
                bounds,
            )
            self.con.execute("COMMIT")
        except duckdb.Error:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.unregister("df_view")

    @staticmethod
    def get_database_file_chain(chain_id: int, path=DEFAULT_TIMESTAMP_CACHE_FOLDER) -> Path:
        """Get the default database file path for a given chain ID."""
        if path.exists():
            assert path.is_dir(), f"Expected directory path, got {path}"
        return path / f"{chain_id}-timestamps.duckdb"

    @staticmethod
    def load(chain_id: int, path: Path) -> "BlockTimestampDatabase":
        """Open an existing timestamp file.

        Initialisation performs the same schema checks as creating a cache.

        :param chain_id: EVM chain identifier.
        :param path: Path to the DuckDB file.
        :return: Open timestamp database.
        """
        return BlockTimestampDatabase(chain_id, path)

    @staticmethod
    def create(chain_id: int, path: Path) -> "BlockTimestampDatabase":
        """Open a persistent per-chain timestamp cache.

        The directory and table are created when absent; an existing file is
        reused and its legacy constraints are migrated transactionally.

        :param chain_id: EVM chain identifier.
        :param path: Directory containing the per-chain DuckDB file.
        :return: Open cache database.
        """
        file = BlockTimestampDatabase.get_database_file_chain(chain_id, path)
        return BlockTimestampDatabase(chain_id, file)

    def save(self) -> None:
        """Commit pending timestamp writes.

        Imports already commit each chunk; this method remains available for
        callers using the older explicit-save interface. It does not issue a
        DuckDB ``CHECKPOINT``.

        :return: ``None`` after committing.
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

    def get_first_block(self) -> int:
        """Get the first block number we have for a given chain ID.

        :return: 0 if no data
        """
        res = self.con.execute(
            """
            SELECT MIN(block_number)
            FROM block_timestamps
        """
        ).fetchone()

        if res is None or res[0] is None:
            return 0
        return res[0]

    def get_last_block(self) -> int:
        """Get the last block number we have for a given chain ID.

        :return: 0 if no data
        """
        res = self.con.execute(
            """
            SELECT MAX(block_number)
            FROM block_timestamps
        """
        ).fetchone()

        if res is None or res[0] is None:
            return 0
        return res[0]

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

    def query(self, start_block: int, end_block: int) -> pd.Series:
        """Get timestamps for a single chain in an inclusive block range.

        Returns a Pandas Series to maintain compatibility with the original API.

        :param start_block: Inclusive start block
        :param end_block: Inclusive end block

        :return:
            Pandas series block number (int) -> block timestamp (pd.Timestamp)
        """
        if start_block > end_block:
            message = "start_block must be <= end_block"
            raise ValueError(message)

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
            return pd.Series([])

        df.set_index("block_number", inplace=True)
        return self.transform_time_values(df["timestamp"])

    @staticmethod
    def transform_time_values(series: pd.Series) -> pd.Series:
        """Convert cached Unix seconds to naive UTC timestamps.

        Keep block numbers as the Series index and second-level precision
        without loading any additional cache rows.

        :param series: Series of integer Unix seconds indexed by block number.
        :return: Series of ``datetime64[s]`` timestamps with the same index.
        """
        return pd.to_datetime(series, unit="s").astype("datetime64[s]")

    def get_count(self) -> int:
        """Count cached block timestamps.

        Query DuckDB without loading timestamp values into Python.

        :return: Number of persisted block rows.
        """
        return self.con.execute(
            """
            SELECT COUNT(*) FROM block_timestamps
            """
        ).fetchone()[0]

    def get_missing_block_numbers(self, block_numbers: Iterable[int]) -> list[int]:
        """Return requested block numbers absent from this cache.

        The lookup is performed as a DuckDB anti-join so sparse historical
        scans do not need to materialise a multi-million-row cache in Pandas.

        :param block_numbers:
            Exact EVM block numbers needed by a caller.
        :return:
            Missing block numbers in ascending order.
        """

        requested = tuple(dict.fromkeys(block_numbers))
        if not requested:
            return []
        requested_df = pd.DataFrame({"block_number": requested})
        self.con.register("requested_block_numbers", requested_df)
        try:
            rows = self.con.execute(
                """
                SELECT requested.block_number
                FROM requested_block_numbers AS requested
                LEFT JOIN block_timestamps AS cached
                    ON cached.block_number = requested.block_number
                WHERE cached.block_number IS NULL
                ORDER BY requested.block_number
                """
            ).fetchall()
        finally:
            self.con.unregister("requested_block_numbers")
        return [int(row[0]) for row in rows]

    def find_gaps(self) -> list[tuple[int, int, int]]:
        """Find all gaps in the block timestamp database.

        Uses LEAD window function for efficient gap boundary detection
        without materialising the full expected block range.

        :return:
            List of ``(gap_start, gap_end, gap_size)`` tuples.
            ``gap_start`` is the last present block before the gap,
            ``gap_end`` is the first present block after the gap,
            ``gap_size`` is the number of missing blocks.
        """
        rows = self.con.execute("""
            SELECT block_number AS gap_start,
                   next_block AS gap_end,
                   (next_block - block_number - 1) AS gap_size
            FROM (
                SELECT block_number,
                       LEAD(block_number) OVER (ORDER BY block_number) AS next_block
                FROM block_timestamps
            )
            WHERE next_block - block_number > 1
            ORDER BY block_number
        """).fetchall()
        return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    def get_slicer(self) -> "BlockTimestampSlicer":
        """Expose this cache through incremental timestamp lookups.

        The slicer owns the open database connection and closes it when its
        ``close()`` method is called.

        :return: Cache-backed timestamp accessor.
        """
        return BlockTimestampSlicer(self)

    def close(self) -> None:
        """Release duckdb resources."""
        logger.info("Closing %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def is_closed(self) -> bool:
        """Check if the database connection is closed."""
        return self.con is None


class BlockTimestampSlicer:
    """Read timestamps from DuckDB in slices iteratively.

    - Maintain a memory buffer of block numbers
    - Avoid reading all Arbitrum 20 GB of timestamp data to memory at once
    """

    def __init__(self, timestamp_db: BlockTimestampDatabase, slice_size: int = 1_000_000):
        self.timestamp_db = timestamp_db
        self.slice_size = slice_size
        self.current_slice: pd.Series = None

    def __len__(self):
        return self.timestamp_db.get_count()

    def __getitem__(self, block_number: int) -> datetime.datetime:
        """Array access to timestamps."""
        value = self.get(block_number)
        if value is None:
            first, last = self.timestamp_db.get_first_and_last_block()
            total = self.timestamp_db.get_count()
            expected = last - first + 1 if last > first else 0
            missing = expected - total
            raise KeyError(f"Block number {block_number:,} not found in timestamp database. Available range: {first:,} - {last:,}, total {total:,} timestamp records, {missing:,} blocks missing in range ({missing / expected * 100:.1f}% gaps). The _get_nearest fallback also failed — check warnings above for gap details. Run scripts/erc-4626/heal-timestamps.py to repair gaps in the timestamp database.")
        return value

    def get(self, block_number: int) -> datetime.datetime | None:
        """Get timestamp for a given block number, or None if not found.

        If the exact block is missing (gap in HyperSync data),
        returns the timestamp of the nearest available block in the current slice.
        """

        assert not self.timestamp_db.is_closed(), "BlockTimestampSlicer.get(): underlying database is already closed"

        if self.current_slice is not None and block_number in self.current_slice:
            return self.current_slice[block_number]

        current_slice_start = -1
        current_slice_end = -1
        if self.current_slice is not None:
            if len(self.current_slice) > 0:
                current_slice_start = self.current_slice.index[0]
                current_slice_end = self.current_slice.index[-1]
            else:
                current_slice_start = 0
                current_slice_end = 0

        logger.debug(f"Querying slice for block {block_number:,} (size {self.slice_size}), current slice is {current_slice_start:,} - {current_slice_end:,}")

        self.current_slice = self.timestamp_db.query(block_number, block_number + self.slice_size)

        try:
            return self.current_slice[block_number]
        except KeyError:
            return self._get_nearest(block_number)

    def _get_nearest(self, block_number: int, max_distance: int = 200) -> datetime.datetime | None:
        """Find the nearest available block timestamp when the exact block is missing.

        Handles gaps in HyperSync data on fast chains like Monad.

        :param max_distance:
            Maximum block distance to tolerate.
            Returns None if the nearest available block is further than this.
            At 200 blocks, worst-case timestamp error is ~6.7 min on Monad (2s blocks)
            or ~40 min on Ethereum (12s blocks).
        """
        if self.current_slice is None or len(self.current_slice) == 0:
            return None

        idx = self.current_slice.index.searchsorted(block_number)

        # Determine the gap boundaries (blocks before and after the missing block)
        # Cast to int to avoid numpy uint64 underflow in subtraction
        if idx >= len(self.current_slice):
            gap_start_block = int(self.current_slice.index[-1])
            gap_start_ts = self.current_slice.iloc[-1]
            gap_end_block = None
            gap_end_ts = None
            nearest_block = gap_start_block
            nearest = gap_start_ts
        elif idx == 0:
            gap_start_block = None
            gap_start_ts = None
            gap_end_block = int(self.current_slice.index[0])
            gap_end_ts = self.current_slice.iloc[0]
            nearest_block = gap_end_block
            nearest = gap_end_ts
        else:
            gap_start_block = int(self.current_slice.index[idx - 1])
            gap_start_ts = self.current_slice.iloc[idx - 1]
            gap_end_block = int(self.current_slice.index[idx])
            gap_end_ts = self.current_slice.iloc[idx]
            # Pick whichever neighbour is closer
            if block_number - gap_start_block <= gap_end_block - block_number:
                nearest_block = gap_start_block
                nearest = gap_start_ts
            else:
                nearest_block = gap_end_block
                nearest = gap_end_ts

        distance = abs(block_number - nearest_block)

        # Format gap info for human-readable messages
        if gap_start_block is not None and gap_end_block is not None:
            gap_size = gap_end_block - gap_start_block
            gap_desc = f"Gap of {gap_size:,} blocks: {gap_start_block:,} ({gap_start_ts}) to {gap_end_block:,} ({gap_end_ts})"
        elif gap_start_block is not None:
            gap_desc = f"Missing block is beyond last available block {gap_start_block:,} ({gap_start_ts})"
        else:
            gap_desc = f"Missing block is before first available block {gap_end_block:,} ({gap_end_ts})"

        if distance > max_distance:
            logger.warning(
                "Block %d not found in timestamp database. Nearest block %d is %d blocks away (exceeds max_distance %d). %s. Run scripts/erc-4626/heal-timestamps.py to repair gaps.",
                block_number,
                nearest_block,
                distance,
                max_distance,
                gap_desc,
            )
            return None

        logger.warning(
            "Block %d not found in timestamp database, using nearest block %d (%d blocks away). %s",
            block_number,
            nearest_block,
            distance,
            gap_desc,
        )
        return nearest

    def get_last_block(self) -> int:
        """Get the maximum block number in the database."""
        return self.timestamp_db.get_last_block()

    def close(self):
        """Release the associated cache db."""
        self.timestamp_db.close()


def load_timestamp_cache(chain_id: int, cache_folder: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER) -> BlockTimestampDatabase:
    """Load the block->timestamp cache for a given chain ID."""
    cache_file = BlockTimestampDatabase.get_database_file_chain(chain_id, cache_folder)
    logger.info(f"Loading block timestamps from {cache_file}")
    db = BlockTimestampDatabase.load(chain_id, cache_file)
    logger.info(f"Database has {db.get_count():,} block timestamps for chain {chain_id}")
    return db
