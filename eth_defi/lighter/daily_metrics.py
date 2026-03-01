"""Lighter daily pool metrics with DuckDB storage.

This module provides a daily pipeline for scanning Lighter pool
metrics and storing them in a DuckDB database.

The pipeline:

1. Bulk-fetches all pools from ``/api/v1/publicPoolsMetadata``
2. Filters by TVL and open status
3. Fetches per-pool share price history via ``/api/v1/account``
4. Stores daily prices and metadata in DuckDB

Example::

    from eth_defi.lighter.session import create_lighter_session
    from eth_defi.lighter.daily_metrics import run_daily_scan

    session = create_lighter_session()
    db = run_daily_scan(session, min_tvl=1_000, max_pools=100)
    print(f"Stored metrics for {db.get_pool_count()} pools")
    db.close()

"""

import datetime
import logging
from pathlib import Path

import duckdb
import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.constants import LIGHTER_DAILY_METRICS_DATABASE
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.vault import (
    LighterPoolSummary,
    fetch_all_pools,
    fetch_pool_detail,
    pool_detail_to_daily_dataframe,
)

logger = logging.getLogger(__name__)


class LighterDailyMetricsDatabase:
    """DuckDB database for storing Lighter pool daily metrics.

    Stores two tables:

    - ``pool_metadata``: Pool information (name, description, fees, TVL, etc.)
    - ``pool_daily_prices``: Daily share price time series with returns

    :param path:
        Path to the DuckDB database file.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self._init_schema()

    def _init_schema(self):
        """Create tables if they do not exist."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS pool_metadata (
                account_index BIGINT PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                l1_address VARCHAR,
                is_llp BOOLEAN DEFAULT FALSE,
                operator_fee DOUBLE,
                total_asset_value DOUBLE,
                annual_percentage_yield DOUBLE,
                sharpe_ratio DOUBLE,
                created_at TIMESTAMP,
                last_updated TIMESTAMP NOT NULL
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS pool_daily_prices (
                account_index BIGINT NOT NULL,
                date DATE NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE,
                daily_return DOUBLE,
                annual_percentage_yield DOUBLE,
                PRIMARY KEY (account_index, date)
            )
        """)

    def upsert_pool_metadata(
        self,
        account_index: int,
        name: str,
        description: str | None = None,
        l1_address: str | None = None,
        is_llp: bool = False,
        operator_fee: float | None = None,
        total_asset_value: float | None = None,
        annual_percentage_yield: float | None = None,
        sharpe_ratio: float | None = None,
        created_at: datetime.datetime | None = None,
    ):
        """Insert or update pool metadata.

        :param account_index:
            Pool account index (primary key).
        :param name:
            Pool display name.
        :param description:
            Pool description text.
        :param l1_address:
            L1 Ethereum address.
        :param is_llp:
            Whether this is the LLP protocol pool.
        :param operator_fee:
            Operator fee percentage.
        :param total_asset_value:
            Total value locked in USDC.
        :param annual_percentage_yield:
            Current APY.
        :param sharpe_ratio:
            Risk-adjusted return metric.
        :param created_at:
            Pool creation timestamp.
        """
        self.con.execute(
            """
            INSERT INTO pool_metadata (
                account_index, name, description, l1_address, is_llp,
                operator_fee, total_asset_value, annual_percentage_yield,
                sharpe_ratio, created_at, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_index) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                l1_address = excluded.l1_address,
                is_llp = excluded.is_llp,
                operator_fee = excluded.operator_fee,
                total_asset_value = excluded.total_asset_value,
                annual_percentage_yield = excluded.annual_percentage_yield,
                sharpe_ratio = excluded.sharpe_ratio,
                created_at = excluded.created_at,
                last_updated = excluded.last_updated
            """,
            [
                account_index,
                name,
                description,
                l1_address,
                is_llp,
                operator_fee,
                total_asset_value,
                annual_percentage_yield,
                sharpe_ratio,
                created_at,
                native_datetime_utc_now(),
            ],
        )

    def upsert_daily_prices(
        self,
        rows: list[tuple],
        cutoff_date: datetime.date | None = None,
    ):
        """Bulk upsert daily price rows for a pool.

        :param rows:
            List of tuples: ``(account_index, date, share_price, tvl, daily_return, annual_percentage_yield)``.
        :param cutoff_date:
            If provided, only store rows up to this date (inclusive).
            Used for incremental scanning / testing.
        """
        if cutoff_date is not None:
            rows = [r for r in rows if r[1] <= cutoff_date]

        if not rows:
            return

        self.con.executemany(
            """
            INSERT INTO pool_daily_prices (
                account_index, date, share_price, tvl,
                daily_return, annual_percentage_yield
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_index, date) DO UPDATE SET
                share_price = excluded.share_price,
                tvl = excluded.tvl,
                daily_return = excluded.daily_return,
                annual_percentage_yield = excluded.annual_percentage_yield
            """,
            rows,
        )

    def get_all_daily_prices(self) -> pd.DataFrame:
        """Retrieve all daily price data.

        :return:
            DataFrame with columns: account_index, date, share_price, tvl,
            daily_return, annual_percentage_yield.
        """
        return self.con.execute("SELECT * FROM pool_daily_prices ORDER BY account_index, date").fetchdf()

    def get_pool_daily_prices(self, account_index: int) -> pd.DataFrame:
        """Get daily prices for a specific pool.

        :param account_index:
            Pool account index.
        :return:
            DataFrame with daily price data for the pool.
        """
        return self.con.execute(
            "SELECT * FROM pool_daily_prices WHERE account_index = ? ORDER BY date",
            [account_index],
        ).fetchdf()

    def get_all_pool_metadata(self) -> pd.DataFrame:
        """Retrieve all pool metadata ordered by TVL.

        :return:
            DataFrame with pool metadata.
        """
        return self.con.execute("SELECT * FROM pool_metadata ORDER BY total_asset_value DESC").fetchdf()

    def get_pool_count(self) -> int:
        """Get number of pools with daily price data.

        :return:
            Count of unique pools.
        """
        result = self.con.execute("SELECT COUNT(DISTINCT account_index) FROM pool_daily_prices").fetchone()
        return result[0] if result else 0

    def get_pool_daily_price_count(self, account_index: int) -> int:
        """Get number of daily price records for a specific pool.

        :param account_index:
            Pool account index.
        :return:
            Count of daily price records.
        """
        result = self.con.execute(
            "SELECT COUNT(*) FROM pool_daily_prices WHERE account_index = ?",
            [account_index],
        ).fetchone()
        return result[0] if result else 0

    def get_pool_last_date(self, account_index: int) -> datetime.date | None:
        """Get the latest date with price data for a pool.

        :param account_index:
            Pool account index.
        :return:
            Latest date or ``None`` if no data.
        """
        result = self.con.execute(
            "SELECT MAX(date) FROM pool_daily_prices WHERE account_index = ?",
            [account_index],
        ).fetchone()
        if result and result[0] is not None:
            val = result[0]
            if isinstance(val, datetime.date):
                return val
            return val.date() if hasattr(val, "date") else val
        return None

    def save(self):
        """Force checkpoint to disk."""
        self.con.execute("CHECKPOINT")

    def close(self):
        """Close database connection."""
        logger.info("Closing daily metrics database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None


def fetch_and_store_pool(
    session: LighterSession,
    db: LighterDailyMetricsDatabase,
    summary: LighterPoolSummary,
    cutoff_date: datetime.date | None = None,
    timeout: float = 30.0,
) -> bool:
    """Fetch a single pool's details and store metrics in the database.

    :param session:
        HTTP session with rate limiting.
    :param db:
        The metrics database to write into.
    :param summary:
        Pool summary from the bulk listing.
    :param cutoff_date:
        If provided, only store price data up to this date.
    :param timeout:
        HTTP request timeout.
    :return:
        ``True`` if the pool was successfully processed.
    """
    try:
        detail = fetch_pool_detail(session, summary.account_index, timeout=timeout)
    except Exception as e:
        logger.warning(
            "Failed to fetch pool details for %s (%d): %s",
            summary.name,
            summary.account_index,
            e,
        )
        return False

    daily_df = pool_detail_to_daily_dataframe(detail)
    if daily_df.empty:
        logger.debug(
            "Skipping pool %s (%d): empty share price history",
            summary.name,
            summary.account_index,
        )
        return False

    # Store metadata
    db.upsert_pool_metadata(
        account_index=summary.account_index,
        name=detail.name or summary.name,
        description=detail.description,
        l1_address=summary.l1_address,
        is_llp=summary.is_llp,
        operator_fee=detail.operator_fee,
        total_asset_value=summary.total_asset_value,
        annual_percentage_yield=detail.annual_percentage_yield,
        sharpe_ratio=detail.sharpe_ratio,
        created_at=summary.created_at,
    )

    # Build daily price rows
    rows = []
    for date_val, row_data in daily_df.iterrows():
        rows.append(
            (
                summary.account_index,
                date_val,
                row_data["share_price"],
                summary.total_asset_value,
                row_data["daily_return"],
                summary.annual_percentage_yield,
            )
        )

    db.upsert_daily_prices(rows, cutoff_date=cutoff_date)

    logger.debug(
        "Stored %d daily prices for pool %s (%d)",
        len(rows),
        summary.name,
        summary.account_index,
    )
    return True


def _process_pool_worker(
    session: LighterSession,
    db: LighterDailyMetricsDatabase,
    summary: LighterPoolSummary,
    cutoff_date: datetime.date | None,
    timeout: float,
) -> bool:
    """Worker function for parallel pool processing."""
    return fetch_and_store_pool(session, db, summary, cutoff_date=cutoff_date, timeout=timeout)


def run_daily_scan(
    session: LighterSession,
    db_path: Path = LIGHTER_DAILY_METRICS_DATABASE,
    min_tvl: float = 1_000,
    max_pools: int = 200,
    max_workers: int = 16,
    cutoff_date: datetime.date | None = None,
    timeout: float = 30.0,
    pool_indices: list[int] | None = None,
) -> LighterDailyMetricsDatabase:
    """Run the daily Lighter pool metrics scan.

    1. Bulk-fetches all pools from ``publicPoolsMetadata``
    2. Filters by TVL and pool limit (or by explicit index list)
    3. Fetches per-pool details and share price history in parallel
    4. Stores everything in DuckDB

    :param session:
        HTTP session with rate limiting.
    :param db_path:
        Path to the DuckDB database file.
    :param min_tvl:
        Minimum TVL in USDC to include a pool.
        Ignored when ``pool_indices`` is provided.
    :param max_pools:
        Maximum number of pools to process (sorted by TVL descending).
        Ignored when ``pool_indices`` is provided.
    :param max_workers:
        Number of parallel workers for fetching pool details.
    :param cutoff_date:
        If provided, only store price data up to this date.
        Used for incremental scanning / testing.
    :param timeout:
        HTTP request timeout.
    :param pool_indices:
        If provided, only scan these specific pool account indices.
        Overrides ``min_tvl`` and ``max_pools`` filters.
    :return:
        The metrics database instance.
    """
    db = LighterDailyMetricsDatabase(db_path)

    # Fetch all pools from bulk listing
    for attempt in range(3):
        try:
            all_pools = fetch_all_pools(session, timeout=timeout)
            break
        except Exception as e:
            if attempt < 2:
                logger.warning("Failed to fetch pool listing (attempt %d/3): %s", attempt + 1, e)
            else:
                raise

    logger.info("Fetched %d pools from Lighter", len(all_pools))

    # Filter pools
    if pool_indices is not None:
        index_set = set(pool_indices)
        filtered = [p for p in all_pools if p.account_index in index_set]

        missing = index_set - {p.account_index for p in filtered}
        if missing:
            logger.warning("Pool indices not found in listing: %s", missing)
    else:
        filtered = [p for p in all_pools if p.total_asset_value >= min_tvl]
        logger.info("After filtering (min_tvl=$%s): %d pools", f"{min_tvl:,.0f}", len(filtered))

        # Sort by TVL descending and limit
        filtered.sort(key=lambda s: s.total_asset_value, reverse=True)
        filtered = filtered[:max_pools]

    logger.info("Processing %d pools", len(filtered))

    # Fetch details and store in parallel
    desc = "Fetching Lighter pool details"
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_process_pool_worker)(session, db, summary, cutoff_date, timeout) for summary in tqdm(filtered, desc=desc))

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)

    db.save()

    logger.info(
        "Daily scan complete. Processed %d pools (%d successful, %d failed) into %s",
        len(filtered),
        success_count,
        fail_count,
        db_path,
    )

    return db
