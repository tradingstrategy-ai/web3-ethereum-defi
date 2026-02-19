"""Hyperliquid daily vault metrics with DuckDB storage.

This module provides a daily pipeline for scanning Hyperliquid native vault
metrics and storing them in a DuckDB database. It computes ERC-4626-style
share prices using the time-weighted return approach, reusing the share
price calculation from :py:mod:`eth_defi.hyperliquid.combined_analysis`.

The pipeline:

1. Bulk-fetches all vaults from the stats-data API
2. Filters by TVL and open status
3. Fetches per-vault portfolio history via ``vaultDetails``
4. Computes share prices from portfolio history
5. Stores daily prices and metadata in DuckDB

Example::

    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.daily_metrics import run_daily_scan, HyperliquidDailyMetricsDatabase

    session = create_hyperliquid_session(requests_per_second=2.75)
    db = run_daily_scan(session, min_tvl=10_000, max_vaults=500)
    print(f"Stored metrics for {db.get_vault_count()} vaults")
    db.close()

"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress
from joblib import Parallel, delayed
from requests import Session
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.combined_analysis import _calculate_share_price
from eth_defi.hyperliquid.vault import (
    HyperliquidVault,
    PortfolioHistory,
    VaultInfo,
    VaultSummary,
    fetch_all_vaults,
)

logger = logging.getLogger(__name__)

#: Synthetic in-house chain ID for Hypercore (Hyperliquid's native non-EVM layer).
#:
#: Uses a negative number to avoid collision with any real EVM chain ID.
#: This is distinct from HyperEVM (chain ID 999) which is the EVM-compatible sidechain.
HYPERCORE_CHAIN_ID: int = -1

#: Default path for Hyperliquid daily metrics database
HYPERLIQUID_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "hyperliquid" / "daily-metrics.duckdb"


def portfolio_to_combined_dataframe(portfolio_all_time: PortfolioHistory) -> pd.DataFrame:
    """Convert vaultDetails portfolio history into a combined DataFrame with share prices.

    Derives ``pnl_update`` and ``netflow_update`` from the portfolio history,
    then feeds them through
    :py:func:`~eth_defi.hyperliquid.combined_analysis._calculate_share_price`
    to compute ERC-4626-style share prices.

    The derivation:

    - ``pnl_update[i] = pnl_history[i] - pnl_history[i-1]``
    - ``netflow_update[i] = (account_value[i] - account_value[i-1]) - pnl_update[i]``
    - ``cumulative_account_value[i] = account_value[i]``

    :param portfolio_all_time:
        The ``allTime`` :py:class:`~eth_defi.hyperliquid.vault.PortfolioHistory`
        from ``vaultDetails`` endpoint.

    :return:
        DataFrame with timestamp index and columns:
        ``pnl_update``, ``netflow_update``, ``cumulative_pnl``,
        ``cumulative_netflow``, ``cumulative_account_value``,
        ``total_assets``, ``total_supply``, ``share_price``
    """

    avh = portfolio_all_time.account_value_history
    pnl = portfolio_all_time.pnl_history

    if len(avh) < 2 or len(pnl) < 2:
        return pd.DataFrame(
            columns=[
                "pnl_update",
                "netflow_update",
                "cumulative_pnl",
                "cumulative_netflow",
                "cumulative_account_value",
                "total_assets",
                "total_supply",
                "share_price",
            ]
        )

    # Build aligned arrays from the portfolio history
    timestamps = []
    pnl_updates = []
    netflow_updates = []
    cumulative_pnls = []
    cumulative_netflows = []
    cumulative_account_values = []

    # Use pnl_history length as the canonical length (should match avh)
    n = min(len(avh), len(pnl))

    for i in range(n):
        ts = avh[i][0]
        av = float(avh[i][1])
        cumulative_pnl = float(pnl[i][1])

        if i == 0:
            pnl_update = cumulative_pnl
            # First entry: netflow = account_value - pnl (initial deposit)
            netflow_update = av - cumulative_pnl
            cumulative_netflow = netflow_update
        else:
            prev_av = float(avh[i - 1][1])
            prev_cumulative_pnl = float(pnl[i - 1][1])

            pnl_update = cumulative_pnl - prev_cumulative_pnl
            av_change = av - prev_av
            netflow_update = av_change - pnl_update
            cumulative_netflow = cumulative_netflows[-1] + netflow_update

        timestamps.append(ts)
        pnl_updates.append(pnl_update)
        netflow_updates.append(netflow_update)
        cumulative_pnls.append(cumulative_pnl)
        cumulative_netflows.append(cumulative_netflow)
        cumulative_account_values.append(av)

    combined = pd.DataFrame(
        {
            "pnl_update": pnl_updates,
            "netflow_update": netflow_updates,
            "cumulative_pnl": cumulative_pnls,
            "cumulative_netflow": cumulative_netflows,
            "cumulative_account_value": cumulative_account_values,
        },
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )

    combined["total_assets"] = combined["cumulative_account_value"]

    # Reuse existing share price calculation from combined_analysis
    combined = _calculate_share_price(combined, initial_balance=0.0)

    return combined


class HyperliquidDailyMetricsDatabase:
    """DuckDB database for storing Hyperliquid vault daily metrics.

    Stores daily share price time series and vault metadata.
    The share prices are computed using time-weighted returns from
    the Hyperliquid API portfolio history.

    Example::

        from pathlib import Path
        from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase

        db = HyperliquidDailyMetricsDatabase(Path("/tmp/metrics.duckdb"))

        # Query vault data
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
                vault_address VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                leader VARCHAR NOT NULL,
                description VARCHAR,
                is_closed BOOLEAN NOT NULL,
                relationship_type VARCHAR NOT NULL,
                create_time TIMESTAMP,
                commission_rate DOUBLE,
                follower_count INTEGER,
                tvl DOUBLE,
                apr DOUBLE,
                last_updated TIMESTAMP NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_daily_prices (
                vault_address VARCHAR NOT NULL,
                date DATE NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE NOT NULL,
                cumulative_pnl DOUBLE,
                daily_pnl DOUBLE,
                daily_return DOUBLE,
                follower_count INTEGER,
                apr DOUBLE,
                PRIMARY KEY (vault_address, date)
            )
        """)

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
    ):
        """Insert or update a vault's metadata.

        :param vault_address:
            Vault address (will be lowercased).
        """
        self.con.execute(
            """
            INSERT INTO vault_metadata (
                vault_address, name, leader, description, is_closed,
                relationship_type, create_time, commission_rate,
                follower_count, tvl, apr, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_address)
            DO UPDATE SET
                name = EXCLUDED.name,
                leader = EXCLUDED.leader,
                description = EXCLUDED.description,
                is_closed = EXCLUDED.is_closed,
                relationship_type = EXCLUDED.relationship_type,
                create_time = EXCLUDED.create_time,
                commission_rate = EXCLUDED.commission_rate,
                follower_count = EXCLUDED.follower_count,
                tvl = EXCLUDED.tvl,
                apr = EXCLUDED.apr,
                last_updated = EXCLUDED.last_updated
            """,
            [
                vault_address.lower(),
                name,
                leader.lower(),
                description,
                is_closed,
                relationship_type,
                create_time,
                commission_rate,
                follower_count,
                tvl,
                apr,
                native_datetime_utc_now(),
            ],
        )

    def upsert_daily_prices(
        self,
        rows: list[tuple],
        cutoff_date: datetime.date | None = None,
    ):
        """Bulk upsert daily price rows for a vault.

        :param rows:
            List of tuples: (vault_address, date, share_price, tvl, cumulative_pnl, daily_pnl, daily_return, follower_count, apr)
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
            INSERT INTO vault_daily_prices (
                vault_address, date, share_price, tvl, cumulative_pnl,
                daily_pnl, daily_return, follower_count, apr
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_address, date)
            DO UPDATE SET
                share_price = EXCLUDED.share_price,
                tvl = EXCLUDED.tvl,
                cumulative_pnl = EXCLUDED.cumulative_pnl,
                daily_pnl = EXCLUDED.daily_pnl,
                daily_return = EXCLUDED.daily_return,
                follower_count = EXCLUDED.follower_count,
                apr = EXCLUDED.apr
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
            ORDER BY vault_address, date
        """).df()

    def get_vault_daily_prices(self, vault_address: HexAddress) -> pd.DataFrame:
        """Get daily price data for a specific vault.

        :param vault_address:
            Vault address to query.
        :return:
            DataFrame with price records for this vault, ordered by date.
        """
        return self.con.execute(
            """
            SELECT * FROM vault_daily_prices
            WHERE vault_address = ?
            ORDER BY date
            """,
            [vault_address.lower()],
        ).df()

    def get_all_vault_metadata(self) -> pd.DataFrame:
        """Get metadata for all vaults.

        :return:
            DataFrame with one row per vault.
        """
        return self.con.execute("SELECT * FROM vault_metadata ORDER BY tvl DESC NULLS LAST").df()

    def get_vault_count(self) -> int:
        """Get the number of unique vaults with price data."""
        return self.con.execute("SELECT COUNT(DISTINCT vault_address) FROM vault_daily_prices").fetchone()[0]

    def get_vault_daily_price_count(self, vault_address: HexAddress) -> int:
        """Get the number of daily price records for a vault.

        :param vault_address:
            Vault address to query.
        :return:
            Number of daily price records.
        """
        return self.con.execute(
            "SELECT COUNT(*) FROM vault_daily_prices WHERE vault_address = ?",
            [vault_address.lower()],
        ).fetchone()[0]

    def get_vault_last_date(self, vault_address: HexAddress) -> datetime.date | None:
        """Get the last date with price data for a vault.

        :param vault_address:
            Vault address to query.
        :return:
            The latest date, or None if no data.
        """
        result = self.con.execute(
            "SELECT MAX(date) FROM vault_daily_prices WHERE vault_address = ?",
            [vault_address.lower()],
        ).fetchone()[0]
        return result

    def save(self):
        """Force a checkpoint to ensure data is written to disk."""
        self.con.commit()

    def close(self):
        """Close the database connection."""
        logger.info("Closing daily metrics database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None


def fetch_and_store_vault(
    session: Session,
    db: HyperliquidDailyMetricsDatabase,
    summary: VaultSummary,
    cutoff_date: datetime.date | None = None,
    timeout: float = 30.0,
) -> bool:
    """Fetch a single vault's details and store metrics in the database.

    :param session:
        HTTP session with rate limiting.
    :param db:
        The metrics database to write into.
    :param summary:
        Vault summary from the bulk listing.
    :param cutoff_date:
        If provided, only store price data up to this date.
    :param timeout:
        HTTP request timeout.
    :return:
        True if the vault was successfully processed.
    """
    vault_address = summary.vault_address.lower()

    try:
        vault = HyperliquidVault(
            session=session,
            vault_address=vault_address,
            timeout=timeout,
        )
        info: VaultInfo = vault.fetch_info()
    except Exception as e:
        logger.warning("Failed to fetch vault details for %s (%s): %s", summary.name, vault_address, e)
        return False

    # Get portfolio history
    portfolio = info.portfolio.get("allTime")
    if portfolio is None or len(portfolio.account_value_history) < 2:
        logger.debug("Skipping vault %s (%s): insufficient portfolio history", summary.name, vault_address)
        return False

    # Compute share prices using existing combined_analysis logic
    combined_df = portfolio_to_combined_dataframe(portfolio)
    if combined_df.empty:
        logger.debug("Skipping vault %s (%s): empty combined DataFrame", summary.name, vault_address)
        return False

    # Store metadata
    follower_count = len(info.followers)
    commission_rate = float(info.commission_rate) if info.commission_rate is not None else None
    apr_val = float(summary.apr) if summary.apr is not None else None

    db.upsert_vault_metadata(
        vault_address=vault_address,
        name=info.name,
        leader=info.leader,
        description=info.description,
        is_closed=info.is_closed,
        relationship_type=info.relationship_type,
        create_time=summary.create_time,
        commission_rate=commission_rate,
        follower_count=follower_count,
        tvl=float(summary.tvl),
        apr=apr_val,
    )

    # Build daily price rows
    rows = []
    prev_share_price = None
    for i, (ts, row_data) in enumerate(zip(combined_df.index, combined_df.itertuples())):
        date_val = ts.date() if hasattr(ts, "date") else ts

        share_price = row_data.share_price
        tvl = row_data.total_assets
        cumulative_pnl = row_data.cumulative_pnl
        daily_pnl = row_data.pnl_update

        if prev_share_price is not None and prev_share_price > 0:
            daily_return = (share_price - prev_share_price) / prev_share_price
        else:
            daily_return = 0.0

        prev_share_price = share_price

        rows.append(
            (
                vault_address,
                date_val,
                share_price,
                tvl,
                cumulative_pnl,
                daily_pnl,
                daily_return,
                follower_count,
                apr_val,
            )
        )

    db.upsert_daily_prices(rows, cutoff_date=cutoff_date)

    logger.debug("Stored %d daily prices for vault %s (%s)", len(rows), info.name, vault_address)
    return True


def _process_vault_worker(
    session: Session,
    db: HyperliquidDailyMetricsDatabase,
    summary: VaultSummary,
    cutoff_date: datetime.date | None,
    timeout: float,
) -> bool:
    """Worker function for parallel vault processing."""
    return fetch_and_store_vault(session, db, summary, cutoff_date=cutoff_date, timeout=timeout)


def run_daily_scan(
    session: Session,
    db_path: Path = HYPERLIQUID_DAILY_METRICS_DATABASE,
    min_tvl: float = 10_000,
    max_vaults: int = 500,
    max_workers: int = 16,
    cutoff_date: datetime.date | None = None,
    timeout: float = 30.0,
) -> HyperliquidDailyMetricsDatabase:
    """Run the daily Hyperliquid vault metrics scan.

    1. Bulk-fetches all vaults from stats-data API
    2. Filters by TVL, open status, and vault limit
    3. Fetches per-vault details and computes share prices
    4. Stores everything in DuckDB

    :param session:
        HTTP session with rate limiting.
        Use :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.
    :param db_path:
        Path to the DuckDB database file.
    :param min_tvl:
        Minimum TVL in USD to include a vault.
    :param max_vaults:
        Maximum number of vaults to process (sorted by TVL descending).
    :param max_workers:
        Number of parallel workers for fetching vault details.
    :param cutoff_date:
        If provided, only store price data up to this date.
        Used for incremental scanning / testing.
    :param timeout:
        HTTP request timeout.
    :return:
        The metrics database instance.
    """
    logger.info("Starting daily Hyperliquid vault scan")

    db = HyperliquidDailyMetricsDatabase(db_path)

    # Fetch all vaults from stats-data API (single GET request)
    vault_summaries = None
    for attempt in range(3):
        try:
            vault_summaries = list(fetch_all_vaults(session, timeout=timeout))
            break
        except Exception as e:
            logger.warning("Error fetching vault summaries (attempt %d/3): %s", attempt + 1, e)
            continue

    if vault_summaries is None:
        raise RuntimeError("Failed to fetch vault summaries after 3 attempts")

    logger.info("Fetched %d total vaults from stats-data API", len(vault_summaries))

    # Filter: TVL threshold, not closed, normal relationship
    filtered = [s for s in vault_summaries if float(s.tvl) >= min_tvl and not s.is_closed]
    logger.info("After filtering (min_tvl=$%s, open only): %d vaults", f"{min_tvl:,.0f}", len(filtered))

    # Sort by TVL descending and limit
    filtered.sort(key=lambda s: float(s.tvl), reverse=True)
    filtered = filtered[:max_vaults]
    logger.info("Processing top %d vaults by TVL", len(filtered))

    # Fetch details and compute prices in parallel
    desc = "Fetching Hyperliquid vault details"
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_process_vault_worker)(session, db, summary, cutoff_date, timeout) for summary in tqdm(filtered, desc=desc))

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)

    db.save()

    logger.info(
        "Daily scan complete. Processed %d vaults (%d successful, %d failed) into %s",
        len(filtered),
        success_count,
        fail_count,
        db_path,
    )

    return db
