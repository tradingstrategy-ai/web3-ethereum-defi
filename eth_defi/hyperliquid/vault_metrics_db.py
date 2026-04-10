"""Base class for Hyperliquid vault metrics DuckDB databases.

Provides the shared ``vault_metadata`` table schema and common methods
used by both the daily and high-frequency pipelines:

- :py:class:`~eth_defi.hyperliquid.daily_metrics.HyperliquidDailyMetricsDatabase`
- :py:class:`~eth_defi.hyperliquid.high_freq_metrics.HyperliquidHighFreqMetricsDatabase`

Subclasses implement their own price table (``vault_daily_prices`` vs
``vault_high_freq_prices``) and upsert/tombstone methods.
"""

import datetime
import logging
from pathlib import Path

import duckdb
import pandas as pd
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


class HyperliquidMetricsDatabaseBase:
    """Base class for Hyperliquid vault metrics databases.

    Manages the shared ``vault_metadata`` table and provides common
    metadata methods.  Subclasses must set :py:attr:`price_table` and
    :py:attr:`time_column` and implement :py:meth:`_init_price_schema`.

    Thread safety
    ~~~~~~~~~~~~~

    The scanners in :py:mod:`~eth_defi.hyperliquid.daily_metrics` and
    :py:mod:`~eth_defi.hyperliquid.high_freq_metrics` call a shared
    database instance from multiple worker threads via
    ``joblib.Parallel(backend="threading")``.  DuckDB's Python binding
    is only thread-safe when each thread uses its **own cursor** — a
    shared ``self.con`` sees result sets clobbered by interleaved
    ``execute()`` calls and raises ``Invalid Input Error: No open
    result set``.

    Every method reachable from worker threads therefore issues its
    query through ``self.con.cursor()`` so that each call gets an
    isolated result set.  Schema init (``_init_*_schema``) and
    ``save()``/``close()`` stay on the base connection because they
    run single-threaded.
    """

    #: Name of the price table (set by subclass).
    price_table: str = ""

    #: Name of the time column in the price table (``"date"`` or ``"timestamp"``).
    time_column: str = ""

    def __init__(self, path: Path):
        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = duckdb.connect(str(path))
        self._init_metadata_schema()
        self._init_price_schema()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_metadata_schema(self):
        """Create the shared vault_metadata table."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                vault_address VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                leader VARCHAR NOT NULL,
                description VARCHAR,
                is_closed BOOLEAN NOT NULL,
                allow_deposits BOOLEAN NOT NULL DEFAULT TRUE,
                relationship_type VARCHAR NOT NULL,
                create_time TIMESTAMP,
                commission_rate DOUBLE,
                follower_count INTEGER,
                tvl DOUBLE,
                apr DOUBLE,
                last_updated TIMESTAMP NOT NULL,
                flow_data_earliest_date DATE
            )
        """)

    def _init_price_schema(self):
        """Create the price table.  Must be overridden by subclasses."""
        raise NotImplementedError

    # ── Metadata methods ──

    def upsert_vault_metadata(
        self,
        vault_address: HexAddress,
        name: str,
        leader: HexAddress,
        description: str | None,
        is_closed: bool,
        relationship_type: str,
        create_time: datetime.datetime | None,
        commission_rate: float | None,
        follower_count: int | None,
        tvl: float | None,
        apr: float | None,
        allow_deposits: bool = True,
        flow_data_earliest_date: datetime.date | None = None,
    ):
        """Insert or update a vault's metadata.

        :param vault_address:
            Vault address (will be lowercased).
        :param flow_data_earliest_date:
            Earliest date for which daily deposit/withdrawal flow data
            has been backfilled.  ``None`` means no flow data yet.
        """
        self.con.cursor().execute(
            """
            INSERT INTO vault_metadata (
                vault_address, name, leader, description, is_closed,
                allow_deposits, relationship_type, create_time, commission_rate,
                follower_count, tvl, apr, last_updated, flow_data_earliest_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_address)
            DO UPDATE SET
                name = EXCLUDED.name,
                leader = EXCLUDED.leader,
                description = EXCLUDED.description,
                is_closed = EXCLUDED.is_closed,
                allow_deposits = EXCLUDED.allow_deposits,
                relationship_type = EXCLUDED.relationship_type,
                create_time = EXCLUDED.create_time,
                commission_rate = EXCLUDED.commission_rate,
                follower_count = EXCLUDED.follower_count,
                tvl = EXCLUDED.tvl,
                apr = EXCLUDED.apr,
                last_updated = EXCLUDED.last_updated,
                flow_data_earliest_date = COALESCE(EXCLUDED.flow_data_earliest_date, vault_metadata.flow_data_earliest_date)
            """,
            [
                vault_address.lower(),
                name,
                leader.lower(),
                description,
                is_closed,
                allow_deposits,
                relationship_type,
                create_time,
                commission_rate,
                follower_count,
                tvl,
                apr,
                native_datetime_utc_now(),
                flow_data_earliest_date,
            ],
        )

    def update_vault_tvl_bulk(
        self,
        updates: list[tuple[float, bool, float | None, str]],
    ):
        """Bulk-update TVL, is_closed, and APR for existing vaults.

        Only updates rows that already exist in ``vault_metadata``.

        :param updates:
            List of tuples ``(tvl, is_closed, apr, vault_address)``.
        """
        if not updates:
            return
        self.con.cursor().executemany(
            """
            UPDATE vault_metadata
            SET tvl = ?,
                is_closed = ?,
                apr = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE vault_address = ?
            """,
            updates,
        )

    def get_all_vault_metadata(self) -> pd.DataFrame:
        """Get metadata for all vaults.

        :return:
            DataFrame with one row per vault.
        """
        return self.con.cursor().execute("SELECT * FROM vault_metadata ORDER BY tvl DESC NULLS LAST").df()

    def get_latest_leader_fractions(self) -> dict[str, float]:
        """Get the latest leader_fraction for each vault.

        Queries the most recent row per vault that has a non-NULL
        ``leader_fraction`` value.

        :return:
            Dict mapping lowercased vault address to leader_fraction.
        """
        rows = (
            self.con.cursor()
            .execute(f"""
            SELECT vault_address, leader_fraction
            FROM {self.price_table}
            WHERE leader_fraction IS NOT NULL
              AND (vault_address, {self.time_column}) IN (
                  SELECT vault_address, MAX({self.time_column})
                  FROM {self.price_table}
                  WHERE leader_fraction IS NOT NULL
                  GROUP BY vault_address
              )
        """)
            .fetchall()
        )
        return {row[0]: row[1] for row in rows}

    # ── Price query methods (use price_table / time_column) ──

    def get_vault_count(self) -> int:
        """Get the number of unique vaults with price data."""
        return self.con.cursor().execute(f"SELECT COUNT(DISTINCT vault_address) FROM {self.price_table}").fetchone()[0]

    def get_recently_tracked_addresses(self, within_days: int = 4) -> set[str]:
        """Return vault addresses with price data within the last *within_days* days.

        Uses a pure date cutoff so that whole-day semantics are preserved
        for the daily table (DATE column) and behave identically for the
        HF table (TIMESTAMP column — DuckDB casts DATE to midnight).

        :param within_days:
            Number of days to look back from today.
        :return:
            Set of lowercased vault addresses.
        """
        cutoff = datetime.date.today() - datetime.timedelta(days=within_days)
        rows = (
            self.con.cursor()
            .execute(
                f"SELECT DISTINCT vault_address FROM {self.price_table} WHERE {self.time_column} >= ?",
                [cutoff],
            )
            .fetchall()
        )
        return {r[0] for r in rows}

    def get_all_tracked_addresses(self) -> set[str]:
        """Return all vault addresses that have any price data.

        :return:
            Set of lowercased vault addresses.
        """
        rows = (
            self.con.cursor()
            .execute(
                f"SELECT DISTINCT vault_address FROM {self.price_table}",
            )
            .fetchall()
        )
        return {r[0] for r in rows}

    # ── Lifecycle methods ──

    def mark_vaults_disappeared(self, known_addresses: set[str]):
        """Set TVL to zero for vaults that disappeared from the API.

        Also writes tombstone price rows so downstream consumers see a
        fresh row reflecting removal.

        :param known_addresses:
            Lowercased vault addresses currently present in the bulk API.
        """
        existing = self.con.cursor().execute("SELECT vault_address FROM vault_metadata").fetchall()

        disappeared = [(0.0, addr[0]) for addr in existing if addr[0] not in known_addresses]

        if not disappeared:
            return

        self.con.cursor().executemany(
            """
            UPDATE vault_metadata
            SET tvl = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE vault_address = ?
            """,
            disappeared,
        )

        disappeared_addrs = [addr for _, addr in disappeared]
        tombstone_count = self._write_tombstone_rows(disappeared_addrs)
        if tombstone_count:
            logger.info(
                "Wrote %d tombstone price rows (TVL=0) for disappeared vaults",
                tombstone_count,
            )

    def tombstone_stale_vaults(
        self,
        known_api_addresses: set[str],
        wind_down_days: int = 4,
    ) -> int:
        """Write tombstone rows for vaults whose wind-down window has expired.

        A vault is eligible for tombstoning when:

        1. It has existing price data in the database
        2. It is NOT present in the current bulk API listing
        3. Its most recent price row is older than ``wind_down_days``
        4. It does not already have a tombstone row

        The tombstone carries forward the last known share_price and
        cumulative_pnl so return calculations are not distorted.

        :param known_api_addresses:
            Vaults still in the API (never tombstoned).
        :param wind_down_days:
            Days after last price row before tombstoning.
        :return:
            Number of tombstone rows written.
        """
        # Use pure date cutoff so whole-day semantics match the original
        # daily pipeline.  DuckDB casts DATE to midnight for TIMESTAMP
        # comparisons, so this works for both table types.
        cutoff = datetime.date.today() - datetime.timedelta(days=wind_down_days)

        candidates = (
            self.con.cursor()
            .execute(
                f"""
            SELECT vault_address
            FROM {self.price_table}
            WHERE vault_address NOT IN (
                SELECT DISTINCT vault_address
                FROM {self.price_table}
                WHERE data_source = 'tombstone'
            )
            GROUP BY vault_address
            HAVING MAX({self.time_column}) < ?
            """,
                [cutoff],
            )
            .fetchall()
        )

        eligible = [addr for (addr,) in candidates if addr not in known_api_addresses]

        count = self._write_tombstone_rows(eligible)
        if count:
            logger.info(
                "Wrote %d tombstone price rows for vaults that fell out of the pipeline",
                count,
            )
        return count

    def _write_tombstone_rows(self, vault_addresses: list[str]) -> int:
        """Write tombstone price rows for the given vaults.

        Carries forward the last known share_price and cumulative_pnl.
        Must be overridden by subclasses to create the correct row type
        and call the correct upsert method.

        :param vault_addresses:
            Lowercased vault addresses to tombstone.
        :return:
            Number of tombstone rows written.
        """
        raise NotImplementedError

    def _get_last_price_row(self, vault_address: str) -> tuple | None:
        """Fetch the last (share_price, cumulative_pnl) for a vault.

        Used by subclass ``_write_tombstone_rows()`` implementations.
        """
        return (
            self.con.cursor()
            .execute(
                f"""
            SELECT share_price, cumulative_pnl
            FROM {self.price_table}
            WHERE vault_address = ?
            ORDER BY {self.time_column} DESC
            LIMIT 1
            """,
                [vault_address],
            )
            .fetchone()
        )

    # ── Persistence ──

    def save(self):
        """Flush pending writes to disk."""
        if self.con:
            self.con.execute("CHECKPOINT")

    def close(self):
        """Close the database connection."""
        if self.con:
            self.con.close()
            self.con = None
