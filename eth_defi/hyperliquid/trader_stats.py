"""DuckDB cache for Hyperliquid trader performance statistics.

Reads from the trade history database (fills, funding, ledger) and
maintains a cache of daily PnL and computed performance metrics
(CAGR, Sharpe, Sortino, Calmar, max drawdown, trades/day, etc.)
in a separate DuckDB file.

Incremental: only recomputes traders whose source data has changed
since the last run, making repeated analysis fast.

Example::

    from pathlib import Path
    from eth_defi.hyperliquid.trader_stats import TraderStatsDatabase

    db = TraderStatsDatabase()
    db.refresh_daily_pnl()
    db.compute_metrics()

    metrics_df = db.get_metrics()
    print(metrics_df.head())

    db.close()

Storage location
----------------

Default: ``~/.tradingstrategy/vaults/hyperliquid/trader-analysis-cache.duckdb``
"""

import logging
from pathlib import Path

import duckdb
import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.api import fetch_portfolio
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.trade_history_db import DEFAULT_TRADE_HISTORY_DB_PATH
from eth_defi.research.perf_metrics import (
    compute_calmar,
    compute_cagr,
    compute_max_drawdown,
    compute_sharpe,
    compute_sortino,
)

logger = logging.getLogger(__name__)

#: Default cache database path
DEFAULT_TRADER_STATS_DB_PATH = Path("~/.tradingstrategy/vaults/hyperliquid/trader-analysis-cache.duckdb").expanduser()

#: Minimum number of fills for a trader to be included in analysis
MIN_FILLS = 10


class TraderStatsDatabase:
    """DuckDB cache for trader performance statistics.

    Reads raw fills, funding, and ledger data from the trade history
    database and maintains three cached tables:

    - ``daily_pnl``: per-trader daily aggregated PnL
    - ``trader_metrics``: computed performance metrics per trader
    - ``cache_metadata``: per-trader staleness tracking

    The :py:meth:`refresh_daily_pnl` method detects which traders
    have new source data and only recomputes those.

    :param cache_path:
        Path to the cache DuckDB file.
    :param source_path:
        Path to the trade history DuckDB
        (from :py:class:`~eth_defi.hyperliquid.trade_history_db.HyperliquidTradeHistoryDatabase`).
    """

    def __init__(
        self,
        cache_path: Path = DEFAULT_TRADER_STATS_DB_PATH,
        source_path: Path = DEFAULT_TRADE_HISTORY_DB_PATH,
    ):
        assert isinstance(cache_path, Path), f"Expected Path for cache_path, got {type(cache_path)}"
        assert isinstance(source_path, Path), f"Expected Path for source_path, got {type(source_path)}"
        assert source_path.exists(), f"Source DB not found: {source_path}. Run sync-trade-history.py first."

        cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.cache_path = cache_path
        self.source_path = source_path
        self.source_con = duckdb.connect(str(source_path), read_only=True)
        self.cache_con = duckdb.connect(str(cache_path))
        self._init_schema()

    def _init_schema(self):
        """Create cache tables if they do not exist."""
        self.cache_con.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                address VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                daily_closed_pnl DOUBLE,
                daily_funding_pnl DOUBLE,
                daily_fees DOUBLE,
                daily_net_pnl DOUBLE,
                PRIMARY KEY (address, trade_date)
            )
        """)

        self.cache_con.execute("""
            CREATE TABLE IF NOT EXISTS trader_metrics (
                address VARCHAR NOT NULL PRIMARY KEY,
                label VARCHAR,
                fill_count INTEGER,
                active_days INTEGER,
                net_pnl DOUBLE,
                total_closed_pnl DOUBLE,
                total_funding_pnl DOUBLE,
                total_fees DOUBLE,
                trades_per_day DOUBLE,
                max_notional_exposure DOUBLE,
                initial_capital DOUBLE,
                cagr DOUBLE,
                sharpe DOUBLE,
                sortino DOUBLE,
                max_drawdown DOUBLE,
                calmar DOUBLE,
                account_created_at BIGINT,
                computed_at BIGINT NOT NULL
            )
        """)

        self.cache_con.execute("""
            CREATE TABLE IF NOT EXISTS cache_metadata (
                address VARCHAR NOT NULL PRIMARY KEY,
                source_newest_fill_ts BIGINT,
                source_fill_count INTEGER,
                last_computed_ts BIGINT NOT NULL
            )
        """)

    def refresh_daily_pnl(self) -> int:
        """Refresh the daily PnL cache for stale traders.

        Compares each trader's fill count and newest fill timestamp
        against what is stored in ``cache_metadata``. Only traders
        whose source data has changed are recomputed.

        :return:
            Number of traders recomputed.
        """
        trader_source_state = self.source_con.execute(
            """
            SELECT
                f.address,
                a.label,
                COUNT(*) as fill_count,
                MAX(f.ts) as newest_fill_ts
            FROM fills f
            INNER JOIN accounts a ON f.address = a.address
            WHERE a.is_vault = FALSE
            GROUP BY f.address, a.label
            HAVING COUNT(*) >= ?
        """,
            [MIN_FILLS],
        ).df()

        cached_state = self.cache_con.execute("""
            SELECT address, source_newest_fill_ts, source_fill_count
            FROM cache_metadata
        """).df()

        # Find stale traders
        if len(cached_state) > 0:
            merged = trader_source_state.merge(cached_state, on="address", how="left")
            stale_mask = merged["source_newest_fill_ts"].isna() | (merged["newest_fill_ts"] != merged["source_newest_fill_ts"]) | (merged["fill_count"] != merged["source_fill_count"])
            stale_traders = merged.loc[stale_mask, "address"].tolist()
        else:
            stale_traders = trader_source_state["address"].tolist()

        logger.info(
            "Traders in source: %d, needing recomputation: %d",
            len(trader_source_state),
            len(stale_traders),
        )

        if not stale_traders:
            return 0

        for address in tqdm(stale_traders, desc="Building daily PnL cache"):
            self._refresh_trader_daily_pnl(address, trader_source_state)

        total_cached = self.cache_con.execute("SELECT COUNT(DISTINCT address) FROM daily_pnl").fetchone()[0]
        logger.info("Daily PnL cache updated for %d traders, total in cache: %d", len(stale_traders), total_cached)
        return len(stale_traders)

    def _refresh_trader_daily_pnl(self, address: str, trader_source_state: pd.DataFrame):
        """Rebuild daily PnL for a single trader."""
        self.cache_con.execute("DELETE FROM daily_pnl WHERE address = ?", [address])

        daily_fills = self.source_con.execute(
            """
            SELECT
                CAST(epoch_ms(ts) AS DATE) as trade_date,
                SUM(closed_pnl) as daily_closed_pnl,
                SUM(fee) as daily_fees
            FROM fills
            WHERE address = ?
            GROUP BY CAST(epoch_ms(ts) AS DATE)
        """,
            [address],
        ).df()

        daily_funding = self.source_con.execute(
            """
            SELECT
                CAST(epoch_ms(ts) AS DATE) as trade_date,
                SUM(usdc) as daily_funding_pnl
            FROM funding
            WHERE address = ?
            GROUP BY CAST(epoch_ms(ts) AS DATE)
        """,
            [address],
        ).df()

        if daily_fills.empty and daily_funding.empty:
            return

        if daily_fills.empty:
            combined = daily_funding.copy()
            combined["daily_closed_pnl"] = 0.0
            combined["daily_fees"] = 0.0
        elif daily_funding.empty:
            combined = daily_fills.copy()
            combined["daily_funding_pnl"] = 0.0
        else:
            combined = daily_fills.merge(daily_funding, on="trade_date", how="outer")
            combined = combined.fillna(0.0)

        combined["daily_net_pnl"] = combined["daily_closed_pnl"] + combined["daily_funding_pnl"] - combined["daily_fees"]
        combined["address"] = address
        combined = combined.sort_values("trade_date")

        self.cache_con.executemany(
            "INSERT OR REPLACE INTO daily_pnl (address, trade_date, daily_closed_pnl, daily_funding_pnl, daily_fees, daily_net_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            combined[["address", "trade_date", "daily_closed_pnl", "daily_funding_pnl", "daily_fees", "daily_net_pnl"]].values.tolist(),
        )

        src_row = trader_source_state.loc[trader_source_state["address"] == address].iloc[0]
        now_ms = int(pd.Timestamp.now().timestamp() * 1000)
        self.cache_con.execute(
            "INSERT OR REPLACE INTO cache_metadata (address, source_newest_fill_ts, source_fill_count, last_computed_ts) VALUES (?, ?, ?, ?)",
            [address, int(src_row["newest_fill_ts"]), int(src_row["fill_count"]), now_ms],
        )

    def fetch_account_ages(
        self,
        session: HyperliquidSession,
        addresses: list[str],
        max_workers: int = 8,
    ) -> dict[str, int]:
        """Fetch account first activity timestamps from the portfolio API.

        Calls the ``portfolio`` info endpoint for each address and extracts
        the first ``pnlHistory`` timestamp. This is aggregated data that
        covers the account's full lifetime, unlike fills which are capped
        at ~10K entries per account.

        Uses ``joblib.Parallel`` with threading backend for concurrent
        API calls.

        :param session:
            Hyperliquid API session.
        :param addresses:
            List of trader addresses to fetch.
        :param max_workers:
            Number of parallel threads.
        :return:
            Dict mapping address to first activity timestamp in
            milliseconds, or empty if the portfolio returned no data.
        """

        def _fetch_one(address: str) -> tuple[str, int | None]:
            portfolio = fetch_portfolio(session, address)
            if portfolio is not None and portfolio.first_activity_at is not None:
                return address, int(portfolio.first_activity_at.timestamp() * 1000)
            return address, None

        results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_fetch_one)(addr) for addr in tqdm(addresses, desc="Fetching account ages"))

        ages = {}
        for address, ts_ms in results:
            if ts_ms is not None:
                ages[address] = ts_ms

        logger.info("Fetched account ages for %d / %d addresses", len(ages), len(addresses))
        return ages

    def compute_metrics(
        self,
        session: HyperliquidSession | None = None,
        max_workers: int = 8,
    ) -> int:
        """Compute performance metrics for all cached traders.

        Reads daily PnL from the cache, combines with fill aggregates
        and deposit data from the source database, and computes
        CAGR, Sharpe, Sortino, Calmar, max drawdown, trades/day,
        and max notional exposure for each trader.

        When *session* is provided, also fetches account first activity
        timestamps from the ``portfolio`` API endpoint and stores them
        as ``account_created_at`` in the metrics table.

        Results are stored in the ``trader_metrics`` table.

        :param session:
            Optional Hyperliquid API session. When provided, account
            ages are fetched from the portfolio endpoint.
        :param max_workers:
            Number of parallel threads for fetching account ages.
        :return:
            Number of traders with computed metrics.
        """
        fill_agg = self.source_con.execute(
            """
            SELECT
                f.address,
                a.label,
                COUNT(*) as fill_count,
                MIN(f.ts) as first_fill_ts,
                MAX(f.ts) as last_fill_ts,
                (MAX(f.ts) - MIN(f.ts)) / (1000.0 * 86400) as active_days,
                MAX(ABS(f.start_position * f.px)) as max_notional_exposure
            FROM fills f
            INNER JOIN accounts a ON f.address = a.address
            WHERE a.is_vault = FALSE
            GROUP BY f.address, a.label
            HAVING COUNT(*) >= ?
        """,
            [MIN_FILLS],
        ).df()

        deposits = self.source_con.execute("""
            SELECT
                l.address,
                SUM(CASE WHEN l.event_type = 'deposit' THEN l.usdc ELSE 0 END) as total_deposits
            FROM ledger l
            INNER JOIN accounts a ON l.address = a.address
            WHERE a.is_vault = FALSE
            GROUP BY l.address
        """).df()

        all_daily_pnl = self.cache_con.execute("""
            SELECT address, trade_date, daily_net_pnl
            FROM daily_pnl
            ORDER BY address, trade_date
        """).df()

        # Fetch account ages from portfolio API if session is provided
        account_ages: dict[str, int] = {}
        if session is not None:
            trader_addresses = all_daily_pnl["address"].unique().tolist()
            account_ages = self.fetch_account_ages(session, trader_addresses, max_workers=max_workers)

        metrics_rows = []

        for address, group in tqdm(all_daily_pnl.groupby("address"), desc="Computing metrics"):
            row = self._compute_trader_metrics(address, group, fill_agg, deposits, account_ages)
            if row is not None:
                metrics_rows.append(row)

        if metrics_rows:
            self.cache_con.execute("DELETE FROM trader_metrics")
            metrics_df = pd.DataFrame(metrics_rows)
            self.cache_con.execute("INSERT INTO trader_metrics SELECT * FROM metrics_df")

        logger.info(
            "Computed metrics for %d traders (%d with CAGR, %d with Sharpe)",
            len(metrics_rows),
            sum(1 for r in metrics_rows if r["cagr"] is not None),
            sum(1 for r in metrics_rows if r["sharpe"] is not None),
        )
        return len(metrics_rows)

    def _compute_trader_metrics(
        self,
        address: str,
        daily_pnl_group: pd.DataFrame,
        fill_agg: pd.DataFrame,
        deposits: pd.DataFrame,
        account_ages: dict[str, int] | None = None,
    ) -> dict | None:
        """Compute metrics for a single trader from daily PnL."""
        group = daily_pnl_group.sort_values("trade_date").reset_index(drop=True)

        fa_row = fill_agg.loc[fill_agg["address"] == address]
        if fa_row.empty:
            return None
        fa = fa_row.iloc[0]

        dep_row = deposits.loc[deposits["address"] == address]
        initial_capital = float(dep_row.iloc[0]["total_deposits"]) if not dep_row.empty and dep_row.iloc[0]["total_deposits"] > 0 else None

        net_pnl = group["daily_net_pnl"].sum()
        days = len(group)
        active_days_val = float(fa["active_days"]) if fa["active_days"] > 0 else 1.0
        trades_per_day = float(fa["fill_count"]) / max(active_days_val, 1.0)

        cagr_val = None
        sharpe_val = None
        sortino_val = None
        max_dd_val = None
        calmar_val = None

        if initial_capital is not None and initial_capital > 0:
            cumulative_pnl = group["daily_net_pnl"].cumsum()
            equity_curve = pd.Series((initial_capital + cumulative_pnl).values, dtype=float)

            if (equity_curve > 0).all():
                daily_returns = equity_curve.pct_change().dropna()

                cagr_val = compute_cagr(float(equity_curve.iloc[0]), float(equity_curve.iloc[-1]), days)
                sharpe_val = compute_sharpe(daily_returns)
                sortino_val = compute_sortino(daily_returns)
                max_dd_val = compute_max_drawdown(equity_curve)
                calmar_val = compute_calmar(cagr_val, max_dd_val)
        else:
            sharpe_val = compute_sharpe(group["daily_net_pnl"])
            sortino_val = compute_sortino(group["daily_net_pnl"])

        pnl_components = self.cache_con.execute(
            """
            SELECT
                SUM(daily_closed_pnl) as total_closed_pnl,
                SUM(daily_funding_pnl) as total_funding_pnl,
                SUM(daily_fees) as total_fees
            FROM daily_pnl WHERE address = ?
        """,
            [address],
        ).fetchone()

        now_ms = int(pd.Timestamp.now().timestamp() * 1000)
        created_at = account_ages.get(address) if account_ages else None
        return {
            "address": address,
            "label": fa["label"],
            "fill_count": int(fa["fill_count"]),
            "active_days": days,
            "net_pnl": net_pnl,
            "total_closed_pnl": pnl_components[0],
            "total_funding_pnl": pnl_components[1],
            "total_fees": pnl_components[2],
            "trades_per_day": trades_per_day,
            "max_notional_exposure": float(fa["max_notional_exposure"]) if pd.notna(fa["max_notional_exposure"]) else None,
            "initial_capital": initial_capital,
            "cagr": cagr_val,
            "sharpe": sharpe_val,
            "sortino": sortino_val,
            "max_drawdown": max_dd_val,
            "calmar": calmar_val,
            "account_created_at": created_at,
            "computed_at": now_ms,
        }

    def get_metrics(self, order_by: str = "cagr DESC NULLS LAST") -> pd.DataFrame:
        """Read computed trader metrics from cache.

        Includes a derived ``account_age_days`` column computed as
        ``(now - account_created_at)`` in days.

        :param order_by:
            SQL ORDER BY clause. Default sorts by CAGR descending.
        :return:
            DataFrame with one row per trader.
        """
        return self.cache_con.execute(f"""
            SELECT
                *,
                CASE WHEN account_created_at IS NOT NULL
                    THEN (epoch_ms(now()) - account_created_at) / (1000.0 * 86400)
                    ELSE NULL
                END as account_age_days
            FROM trader_metrics
            ORDER BY {order_by}
        """).df()

    def get_daily_pnl(self, address: str) -> pd.DataFrame:
        """Read cached daily PnL for a single trader.

        :param address:
            Trader address.
        :return:
            DataFrame with columns: trade_date, daily_net_pnl,
            daily_closed_pnl, daily_funding_pnl, daily_fees.
        """
        return self.cache_con.execute(
            """
            SELECT trade_date, daily_closed_pnl, daily_funding_pnl, daily_fees, daily_net_pnl
            FROM daily_pnl
            WHERE address = ?
            ORDER BY trade_date
        """,
            [address.lower()],
        ).df()

    def get_source_overview(self) -> pd.DataFrame:
        """Get overview counts from the source trade history database.

        :return:
            Single-row DataFrame with total_fills, total_funding,
            total_ledger, total_accounts, trader_accounts, vault_accounts.
        """
        return self.source_con.execute("""
            SELECT
                (SELECT COUNT(*) FROM fills) as total_fills,
                (SELECT COUNT(*) FROM funding) as total_funding,
                (SELECT COUNT(*) FROM ledger) as total_ledger,
                (SELECT COUNT(*) FROM accounts) as total_accounts,
                (SELECT COUNT(*) FROM accounts WHERE is_vault = FALSE) as trader_accounts,
                (SELECT COUNT(*) FROM accounts WHERE is_vault = TRUE) as vault_accounts
        """).df()

    def close(self):
        """Close both database connections."""
        logger.info("Closing trader stats database at %s", self.cache_path)
        if self.source_con is not None:
            self.source_con.close()
            self.source_con = None
        if self.cache_con is not None:
            self.cache_con.close()
            self.cache_con = None

    def __del__(self):
        if hasattr(self, "cache_con") and self.cache_con is not None:
            self.cache_con.close()
            self.cache_con = None
        if hasattr(self, "source_con") and self.source_con is not None:
            self.source_con.close()
            self.source_con = None
