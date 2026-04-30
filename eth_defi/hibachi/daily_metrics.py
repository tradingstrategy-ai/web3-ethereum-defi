"""Hibachi daily vault metrics with DuckDB storage.

This module provides a daily pipeline for scanning Hibachi vault
metrics and storing them in a DuckDB database. It derives daily share
prices from the public ``/vault/performance`` endpoint on the
Hibachi data API.

The pipeline:

1. Fetches vault metadata via ``/vault/info``
2. Fetches per-vault share price history via ``/vault/performance``
3. Stores daily prices and metadata in DuckDB

Example::

    from eth_defi.hibachi.daily_metrics import run_daily_scan, HibachiDailyMetricsDatabase

    db = run_daily_scan()
    print(f"Stored metrics for {db.get_vault_count()} vaults")
    db.close()

"""

import logging
from pathlib import Path

import duckdb
import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hibachi.constants import HIBACHI_DAILY_METRICS_DATABASE
from eth_defi.hibachi.session import HibachiSession
from eth_defi.hibachi.vault import (
    HibachiVaultInfo,
    fetch_vault_info,
    fetch_vault_performance,
)

logger = logging.getLogger(__name__)


class HibachiDailyMetricsDatabase:
    """DuckDB database for storing Hibachi vault daily metrics.

    Stores daily share price time series and vault metadata.
    The share prices come from the Hibachi data API's
    ``/vault/performance`` endpoint.

    Example::

        from pathlib import Path
        from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase

        db = HibachiDailyMetricsDatabase(Path("/tmp/metrics.duckdb"))
        df = db.get_all_daily_prices()
        print(df)
        db.close()

    """

    def __init__(self, path: Path):
        """Initialise the database connection.

        :param path:
            Path to the DuckDB file. Parent directories will be created if needed.
        """
        assert isinstance(path, Path), f"Expected Path for path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        path.parent.mkdir(parents=True, exist_ok=True)

        self.path = path
        self.con = duckdb.connect(str(path))
        self._init_schema()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                vault_id INTEGER PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                short_description VARCHAR,
                description VARCHAR,
                per_share_price DOUBLE,
                outstanding_shares DOUBLE,
                tvl DOUBLE,
                min_unlock_hours INTEGER,
                vault_pub_key VARCHAR,
                vault_asset_id INTEGER,
                last_updated TIMESTAMP NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_daily_prices (
                vault_id INTEGER NOT NULL,
                date DATE NOT NULL,
                per_share_price DOUBLE NOT NULL,
                tvl DOUBLE,
                daily_return DOUBLE,
                written_at TIMESTAMP,
                PRIMARY KEY (vault_id, date)
            )
        """)

    def upsert_vault_metadata(
        self,
        vault_id: int,
        symbol: str,
        short_description: str | None,
        description: str | None,
        per_share_price: float | None,
        outstanding_shares: float | None,
        tvl: float | None,
        min_unlock_hours: int | None,
        vault_pub_key: str | None = None,
        vault_asset_id: int | None = None,
    ):
        """Insert or update a vault's metadata.

        :param vault_id:
            Unique vault ID on the Hibachi platform.
        :param symbol:
            Short ticker symbol (e.g. ``GAV``).
        :param short_description:
            Display name.
        :param description:
            Long description of the vault strategy.
        :param per_share_price:
            Current share price in USDT.
        :param outstanding_shares:
            Total shares issued.
        :param tvl:
            Current TVL in USDT.
        :param min_unlock_hours:
            Minimum lockup period in hours.
        :param vault_pub_key:
            Vault's on-exchange public key.
        :param vault_asset_id:
            Native asset ID of the vault share token.
        """
        self.con.execute(
            """
            INSERT INTO vault_metadata (
                vault_id, symbol, short_description, description,
                per_share_price, outstanding_shares, tvl,
                min_unlock_hours, vault_pub_key, vault_asset_id, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_id)
            DO UPDATE SET
                symbol = EXCLUDED.symbol,
                short_description = EXCLUDED.short_description,
                description = EXCLUDED.description,
                per_share_price = EXCLUDED.per_share_price,
                outstanding_shares = EXCLUDED.outstanding_shares,
                tvl = EXCLUDED.tvl,
                min_unlock_hours = EXCLUDED.min_unlock_hours,
                vault_pub_key = EXCLUDED.vault_pub_key,
                vault_asset_id = EXCLUDED.vault_asset_id,
                last_updated = EXCLUDED.last_updated
            """,
            [
                vault_id,
                symbol,
                short_description,
                description,
                per_share_price,
                outstanding_shares,
                tvl,
                min_unlock_hours,
                vault_pub_key,
                vault_asset_id,
                native_datetime_utc_now(),
            ],
        )

    def upsert_daily_prices(self, rows: list[tuple]):
        """Bulk upsert daily price rows for a vault.

        :param rows:
            List of tuples: ``(vault_id, date, per_share_price, tvl, daily_return, written_at)``.
        """
        if not rows:
            return

        self.con.executemany(
            """
            INSERT INTO vault_daily_prices (
                vault_id, date, per_share_price, tvl, daily_return, written_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_id, date)
            DO UPDATE SET
                per_share_price = EXCLUDED.per_share_price,
                tvl = EXCLUDED.tvl,
                daily_return = EXCLUDED.daily_return,
                written_at = EXCLUDED.written_at
            """,
            rows,
        )

    def get_all_daily_prices(self) -> pd.DataFrame:
        """Get all daily price data across all vaults.

        :return:
            DataFrame with all daily price records, ordered by vault then date.
        """
        return self.con.execute("""
            SELECT * FROM vault_daily_prices
            ORDER BY vault_id, date
        """).df()

    def get_vault_daily_prices(self, vault_id: int) -> pd.DataFrame:
        """Get daily price data for a specific vault.

        :param vault_id:
            Vault ID to query.
        :return:
            DataFrame with price records for this vault, ordered by date.
        """
        return self.con.execute(
            """
            SELECT * FROM vault_daily_prices
            WHERE vault_id = ?
            ORDER BY date
            """,
            [vault_id],
        ).df()

    def get_all_vault_metadata(self) -> pd.DataFrame:
        """Get metadata for all vaults.

        :return:
            DataFrame with one row per vault.
        """
        return self.con.execute("SELECT * FROM vault_metadata ORDER BY tvl DESC NULLS LAST").df()

    def get_vault_count(self) -> int:
        """Get the number of unique vaults with price data."""
        return self.con.execute("SELECT COUNT(DISTINCT vault_id) FROM vault_daily_prices").fetchone()[0]

    def save(self):
        """Force a checkpoint to ensure data is written to disk."""
        self.con.commit()

    def close(self):
        """Close the database connection."""
        logger.info("Closing Hibachi daily metrics database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None


def fetch_and_store_vault(
    session: HibachiSession,
    db: HibachiDailyMetricsDatabase,
    vault_info: HibachiVaultInfo,
    timeout: float = 30.0,
) -> bool:
    """Fetch a single vault's share price history and store in the database.

    :param session:
        HTTP session.
    :param db:
        The metrics database to write into.
    :param vault_info:
        Vault metadata from the listing.
    :param timeout:
        HTTP request timeout.
    :return:
        True if the vault was successfully processed.
    """
    try:
        daily_prices = fetch_vault_performance(
            session,
            vault_id=vault_info.vault_id,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to fetch share price history for %s (vault %d): %s", vault_info.symbol, vault_info.vault_id, e)
        return False

    if not daily_prices:
        logger.debug("Skipping vault %s (vault %d): empty share price history", vault_info.symbol, vault_info.vault_id)
        return False

    # Store metadata
    db.upsert_vault_metadata(
        vault_id=vault_info.vault_id,
        symbol=vault_info.symbol,
        short_description=vault_info.short_description,
        description=vault_info.description,
        per_share_price=vault_info.per_share_price,
        outstanding_shares=vault_info.outstanding_shares,
        tvl=vault_info.tvl,
        min_unlock_hours=vault_info.min_unlock_hours,
        vault_pub_key=vault_info.vault_pub_key,
        vault_asset_id=vault_info.vault_asset_id,
    )

    # Build daily price rows
    written_at = native_datetime_utc_now()
    rows = []
    for dp in daily_prices:
        rows.append(
            (
                dp.vault_id,
                dp.date,
                dp.per_share_price,
                dp.tvl,
                dp.daily_return,
                written_at,
            )
        )

    db.upsert_daily_prices(rows)

    logger.debug("Stored %d daily prices for vault %s (vault %d)", len(rows), vault_info.symbol, vault_info.vault_id)
    return True


def run_daily_scan(
    session: HibachiSession | None = None,
    db_path: Path = HIBACHI_DAILY_METRICS_DATABASE,
    timeout: float = 30.0,
    vault_ids: list[int] | None = None,
) -> HibachiDailyMetricsDatabase:
    """Run the daily Hibachi vault metrics scan.

    1. Fetches vault metadata via ``/vault/info``
    2. Fetches per-vault share price history via ``/vault/performance``
    3. Stores everything in DuckDB

    No authentication is required — all data comes from public endpoints.

    :param session:
        HTTP session. If None, a plain ``requests.Session()`` is created.
    :param db_path:
        Path to the DuckDB database file.
    :param timeout:
        HTTP request timeout.
    :param vault_ids:
        If provided, only scan these specific vault IDs (integers).
        Overrides the default vault listing.
    :return:
        The metrics database instance.
    """
    if session is None:
        from eth_defi.hibachi.session import create_hibachi_session

        session = create_hibachi_session()

    logger.info("Starting daily Hibachi vault scan")

    db = HibachiDailyMetricsDatabase(db_path)

    # Step 1: Discover vaults
    vault_summaries = fetch_vault_info(session, timeout=timeout)

    if vault_ids is not None:
        vault_id_set = set(vault_ids)
        vault_summaries = [s for s in vault_summaries if s.vault_id in vault_id_set]

    logger.info("Processing %d Hibachi vaults", len(vault_summaries))

    # Step 2: Fetch and store share price history per vault
    success_count = 0
    fail_count = 0
    for vault_info in tqdm(vault_summaries, desc="Fetching Hibachi vault details"):
        if fetch_and_store_vault(session, db, vault_info, timeout):
            success_count += 1
        else:
            fail_count += 1

    db.save()

    logger.info(
        "Daily scan complete. Processed %d vaults (%d successful, %d failed) into %s",
        len(vault_summaries),
        success_count,
        fail_count,
        db_path,
    )

    return db
