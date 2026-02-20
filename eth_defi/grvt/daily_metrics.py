"""GRVT daily vault metrics with DuckDB storage.

This module provides a daily pipeline for scanning GRVT vault
metrics and storing them in a DuckDB database. It derives daily share
prices from the public ``vault_summary_history`` endpoint on the
GRVT market data API.

The pipeline:

1. Discovers vaults by scraping the GRVT strategies page
2. Fetches per-vault share price history via ``vault_summary_history``
3. Enriches with TVL from ``vault_detail``
4. Stores daily prices and metadata in DuckDB

Example::

    from eth_defi.grvt.daily_metrics import run_daily_scan, GRVTDailyMetricsDatabase

    db = run_daily_scan()
    print(f"Stored metrics for {db.get_vault_count()} vaults")
    db.close()

"""

import logging
from pathlib import Path

import pandas as pd
from requests import Session
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.grvt.constants import GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.vault import (
    GRVTVaultSummary,
    fetch_vault_details,
    fetch_vault_listing,
    fetch_vault_summary_history,
)

logger = logging.getLogger(__name__)


class GRVTDailyMetricsDatabase:
    """DuckDB database for storing GRVT vault daily metrics.

    Stores daily share price time series and vault metadata.
    The share prices come from the GRVT market data API's
    ``vault_summary_history`` endpoint.

    Example::

        from pathlib import Path
        from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase

        db = GRVTDailyMetricsDatabase(Path("/tmp/metrics.duckdb"))
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

        import duckdb

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
                vault_id VARCHAR PRIMARY KEY,
                chain_vault_id INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                description VARCHAR,
                vault_type VARCHAR,
                manager_name VARCHAR,
                tvl DOUBLE,
                share_price DOUBLE,
                investor_count INTEGER,
                last_updated TIMESTAMP NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_daily_prices (
                vault_id VARCHAR NOT NULL,
                date DATE NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE,
                daily_return DOUBLE,
                PRIMARY KEY (vault_id, date)
            )
        """)

    def upsert_vault_metadata(
        self,
        vault_id: str,
        chain_vault_id: int,
        name: str,
        description: str | None,
        vault_type: str | None,
        manager_name: str | None,
        tvl: float | None,
        share_price: float | None,
        investor_count: int | None,
    ):
        """Insert or update a vault's metadata.

        :param vault_id:
            Vault string ID (e.g. ``VLT:xxx``).
        :param chain_vault_id:
            Numeric on-chain vault ID.
        """
        self.con.execute(
            """
            INSERT INTO vault_metadata (
                vault_id, chain_vault_id, name, description, vault_type,
                manager_name, tvl, share_price, investor_count, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_id)
            DO UPDATE SET
                chain_vault_id = EXCLUDED.chain_vault_id,
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                vault_type = EXCLUDED.vault_type,
                manager_name = EXCLUDED.manager_name,
                tvl = EXCLUDED.tvl,
                share_price = EXCLUDED.share_price,
                investor_count = EXCLUDED.investor_count,
                last_updated = EXCLUDED.last_updated
            """,
            [
                vault_id,
                chain_vault_id,
                name,
                description,
                vault_type,
                manager_name,
                tvl,
                share_price,
                investor_count,
                native_datetime_utc_now(),
            ],
        )

    def upsert_daily_prices(self, rows: list[tuple]):
        """Bulk upsert daily price rows for a vault.

        :param rows:
            List of tuples: ``(vault_id, date, share_price, tvl, daily_return)``.
        """
        if not rows:
            return

        self.con.executemany(
            """
            INSERT INTO vault_daily_prices (
                vault_id, date, share_price, tvl, daily_return
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (vault_id, date)
            DO UPDATE SET
                share_price = EXCLUDED.share_price,
                tvl = EXCLUDED.tvl,
                daily_return = EXCLUDED.daily_return
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

    def get_vault_daily_prices(self, vault_id: str) -> pd.DataFrame:
        """Get daily price data for a specific vault.

        :param vault_id:
            Vault string ID to query.
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
        logger.info("Closing GRVT daily metrics database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None


def fetch_and_store_vault(
    session: Session,
    db: GRVTDailyMetricsDatabase,
    summary: GRVTVaultSummary,
    timeout: float = 30.0,
) -> bool:
    """Fetch a single vault's share price history and store in the database.

    :param session:
        HTTP session (no authentication needed).
    :param db:
        The metrics database to write into.
    :param summary:
        Vault summary from the listing.
    :param timeout:
        HTTP request timeout.
    :return:
        True if the vault was successfully processed.
    """
    try:
        daily_df = fetch_vault_summary_history(
            session,
            chain_vault_id=summary.chain_vault_id,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to fetch share price history for %s (%s): %s", summary.name, summary.vault_id, e)
        return False

    if daily_df.empty:
        logger.debug("Skipping vault %s (%s): empty share price history", summary.name, summary.vault_id)
        return False

    # Store metadata
    db.upsert_vault_metadata(
        vault_id=summary.vault_id,
        chain_vault_id=summary.chain_vault_id,
        name=summary.name,
        description=summary.description,
        vault_type=summary.vault_type,
        manager_name=summary.manager_name,
        tvl=summary.tvl,
        share_price=summary.share_price,
        investor_count=None,
    )

    # Build daily price rows
    rows = []
    for ts, row_data in daily_df.iterrows():
        date_val = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            (
                summary.vault_id,
                date_val,
                row_data["share_price"],
                summary.tvl,
                row_data["daily_return"],
            )
        )

    db.upsert_daily_prices(rows)

    logger.debug("Stored %d daily prices for vault %s (%s)", len(rows), summary.name, summary.vault_id)
    return True


def run_daily_scan(
    session: Session | None = None,
    db_path: Path = GRVT_DAILY_METRICS_DATABASE,
    timeout: float = 30.0,
    vault_ids: list[str] | None = None,
    only_discoverable: bool = True,
) -> GRVTDailyMetricsDatabase:
    """Run the daily GRVT vault metrics scan.

    1. Discovers vaults from the GRVT strategies page
    2. Fetches TVL from the market data API
    3. Fetches per-vault share price history
    4. Stores everything in DuckDB

    No authentication is required — all data comes from public endpoints.

    :param session:
        HTTP session. If None, a plain ``requests.Session()`` is created.
    :param db_path:
        Path to the DuckDB database file.
    :param timeout:
        HTTP request timeout.
    :param vault_ids:
        If provided, only scan these specific vault string IDs.
        Overrides the default vault listing.
    :param only_discoverable:
        If True, only scan vaults marked as discoverable.
    :return:
        The metrics database instance.
    """
    import requests

    if session is None:
        session = requests.Session()

    logger.info("Starting daily GRVT vault scan")

    db = GRVTDailyMetricsDatabase(db_path)

    # Step 1: Discover vaults
    vault_summaries = fetch_vault_listing(
        session,
        only_discoverable=only_discoverable,
        timeout=timeout,
    )

    if vault_ids is not None:
        vault_id_set = set(vault_ids)
        vault_summaries = [s for s in vault_summaries if s.vault_id in vault_id_set]

    # Step 2: Enrich with TVL from vault_detail
    chain_ids = [s.chain_vault_id for s in vault_summaries]
    if chain_ids:
        try:
            details = fetch_vault_details(session, chain_ids, timeout=timeout)
            for summary in vault_summaries:
                detail = details.get(summary.chain_vault_id)
                if detail:
                    summary.tvl = float(detail.get("total_equity", 0) or 0)
                    summary.share_price = float(detail.get("share_price", 0) or 0)
        except Exception as e:
            logger.warning("Failed to fetch vault details: %s", e)

    logger.info("Processing %d GRVT vaults", len(vault_summaries))

    # Step 3: Fetch and store share price history per vault
    success_count = 0
    fail_count = 0
    for summary in tqdm(vault_summaries, desc="Fetching GRVT vault details"):
        if fetch_and_store_vault(session, db, summary, timeout):
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
