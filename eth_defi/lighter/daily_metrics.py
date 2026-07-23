"""Lighter daily pool metrics with DuckDB storage.

This module provides a daily pipeline for scanning Lighter pool
metrics and storing them in a DuckDB database.

The pipeline:

1. Bulk-fetches all pools from ``/api/v1/publicPoolsMetadata``
2. Filters by TVL and open status
3. Fetches per-pool share prices and current account state via ``/api/v1/account``
4. Fetches historical PnL, volume, shares, and flow counters via ``/api/v1/pnl``
5. Stores daily history, current metadata, and append-only snapshots in DuckDB

Example::

    from eth_defi.lighter.session import create_lighter_session
    from eth_defi.lighter.daily_metrics import run_daily_scan

    session = create_lighter_session()
    db = run_daily_scan(session, min_tvl=1_000, max_pools=100)
    print(f"Stored metrics for {db.get_pool_count()} pools")
    db.close()

"""

import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
import requests
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.constants import LIGHTER_DAILY_METRICS_DATABASE, LIGHTER_ETHEREUM
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.vault import (
    LIGHTER_LLP_DESCRIPTION,
    LighterPoolSnapshot,
    LighterPoolSummary,
    fetch_all_pools,
    fetch_pool_daily_pnl_history,
    fetch_pool_detail,
    pool_detail_to_daily_dataframe,
)

logger = logging.getLogger(__name__)


def _optional_dataframe_float(value: object) -> float | None:
    """Convert an optional DataFrame cell to a Python float.

    DuckDB/Pandas use several missing-value representations. Normalise all of
    them to ``None`` before inserting nullable source metrics.

    :param value:
        DataFrame cell value.
    :return:
        Float value, or ``None`` when the cell is missing.
    """
    return float(value) if pd.notna(value) else None


@dataclass(slots=True)
class LighterDailyPriceRow:
    """One Lighter daily price observation ready for DuckDB storage.

    Stores source cumulative counters rather than derived daily flows. This
    preserves valid history across bounded PnL API re-scans.
    """

    #: Lighter pool account index
    account_index: int

    #: UTC calendar date of the share-price observation
    date: datetime.date

    #: Pool share price in the deployment's collateral currency
    share_price: float

    #: Derived total value locked in the deployment's collateral currency
    tvl: float

    #: Price return for the UTC day
    daily_return: float

    #: Current API APY snapshot
    annual_percentage_yield: float

    #: Outstanding pool shares at this date
    total_shares: int | None

    #: Source cumulative pool inflow in the deployment's collateral currency
    cumulative_pool_inflow: float | None

    #: Source cumulative pool outflow in the deployment's collateral currency
    cumulative_pool_outflow: float | None

    #: Naive UTC time when the row was written
    written_at: datetime.datetime

    #: Source cumulative account-level inflow
    cumulative_account_inflow: float | None = None

    #: Source cumulative account-level outflow
    cumulative_account_outflow: float | None = None

    #: Source cumulative spot inflow
    cumulative_spot_inflow: float | None = None

    #: Source cumulative spot outflow
    cumulative_spot_outflow: float | None = None

    #: Source cumulative staking inflow
    cumulative_staking_inflow: float | None = None

    #: Source cumulative staking outflow
    cumulative_staking_outflow: float | None = None

    #: Source trade PnL
    trade_pnl: float | None = None

    #: Source spot-trade PnL
    trade_spot_pnl: float | None = None

    #: Source pool PnL
    pool_pnl: float | None = None

    #: Source staking PnL
    staking_pnl: float | None = None

    #: Source trading volume
    volume: float | None = None

    def as_db_tuple(self) -> tuple[object, ...]:
        """Convert the row to the current DuckDB insert layout."""
        return (
            self.account_index,
            self.date,
            self.share_price,
            self.tvl,
            self.daily_return,
            self.annual_percentage_yield,
            self.total_shares,
            self.cumulative_pool_inflow,
            self.cumulative_pool_outflow,
            self.written_at,
            self.cumulative_account_inflow,
            self.cumulative_account_outflow,
            self.cumulative_spot_inflow,
            self.cumulative_spot_outflow,
            self.cumulative_staking_inflow,
            self.cumulative_staking_outflow,
            self.trade_pnl,
            self.trade_spot_pnl,
            self.pool_pnl,
            self.staking_pnl,
            self.volume,
        )


def _normalise_daily_price_row(
    row: LighterDailyPriceRow | tuple[object, ...],
) -> tuple[object, ...]:
    """Convert current and legacy daily-row inputs to the database layout.

    Deployment-aware callers predating source-accounting fields pass a
    seven-item tuple. Preserve that API while filling the new nullable fields
    with ``None``. New scanner code uses :class:`LighterDailyPriceRow`.

    :param row:
        Typed current row or legacy seven-item tuple.
    :return:
        Twenty-one values matching ``pool_daily_prices`` after ``deployment``.
    """
    if isinstance(row, LighterDailyPriceRow):
        return row.as_db_tuple()

    if len(row) == 21:
        return row
    if len(row) != 7:
        raise ValueError(f"Expected a LighterDailyPriceRow or 7-item legacy tuple, got {len(row)} items")

    account_index, date, share_price, tvl, daily_return, annual_percentage_yield, written_at = row
    return (
        account_index,
        date,
        share_price,
        tvl,
        daily_return,
        annual_percentage_yield,
        None,
        None,
        None,
        written_at,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


class LighterDailyMetricsDatabase:
    """DuckDB database for storing Lighter pool daily metrics.

    Stores three tables:

    - ``pool_metadata``: Pool information (name, description, fees, TVL, etc.)
    - ``pool_daily_prices``: Daily share price time series with returns
    - ``pool_snapshots``: Append-only scan-time account, ownership, risk, and
      exposure observations

    :param path:
        Path to the DuckDB database file.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self._init_schema()

    def _init_schema(self) -> None:
        """Create or migrate the deployment-aware database schema.

        The original schema keyed its tables only by ``account_index``.
        Lighter deployments reuse account indexes, so legacy rows are copied
        transactionally into composite-key tables and labelled ``ethereum``.
        New source fields are nullable and preserve older rows as unknown.
        Any migration failure aborts database opening without discarding the
        original tables.
        """
        if not self._table_exists("pool_metadata"):
            self._create_pool_metadata_table("pool_metadata")
        if not self._table_exists("pool_daily_prices"):
            self._create_pool_daily_prices_table("pool_daily_prices")

        self._ensure_columns(
            "pool_metadata",
            {
                "status": "INTEGER DEFAULT 0",
                "total_shares": "BIGINT",
                "operator_shares": "BIGINT",
            },
        )
        self._ensure_columns(
            "pool_daily_prices",
            {
                "written_at": "TIMESTAMP",
                "total_shares": "BIGINT",
                "cumulative_pool_inflow": "DOUBLE",
                "cumulative_pool_outflow": "DOUBLE",
                "cumulative_account_inflow": "DOUBLE",
                "cumulative_account_outflow": "DOUBLE",
                "cumulative_spot_inflow": "DOUBLE",
                "cumulative_spot_outflow": "DOUBLE",
                "cumulative_staking_inflow": "DOUBLE",
                "cumulative_staking_outflow": "DOUBLE",
                "trade_pnl": "DOUBLE",
                "trade_spot_pnl": "DOUBLE",
                "pool_pnl": "DOUBLE",
                "staking_pnl": "DOUBLE",
                "volume": "DOUBLE",
            },
        )

        metadata_columns = self._get_table_columns("pool_metadata")
        price_columns = self._get_table_columns("pool_daily_prices")
        metadata_needs_migration = "deployment" not in metadata_columns
        prices_need_migration = "deployment" not in price_columns
        if metadata_needs_migration or prices_need_migration:
            logger.info("Migrating legacy Lighter DuckDB schema to deployment-aware composite keys")
            self.con.execute("BEGIN TRANSACTION")
            try:
                if metadata_needs_migration:
                    self._create_pool_metadata_table("pool_metadata_v2")
                    self.con.execute(
                        """
                        INSERT INTO pool_metadata_v2 (
                            deployment, account_index, name, description, l1_address,
                            is_llp, status, operator_fee, total_asset_value,
                            annual_percentage_yield, sharpe_ratio, total_shares,
                            operator_shares, created_at, last_updated
                        )
                        SELECT ?, account_index, name, description, l1_address,
                            is_llp, status, operator_fee, total_asset_value,
                            annual_percentage_yield, sharpe_ratio, total_shares,
                            operator_shares, created_at, last_updated
                        FROM pool_metadata
                        """,
                        [LIGHTER_ETHEREUM.slug],
                    )
                    self.con.execute("DROP TABLE pool_metadata")
                    self.con.execute("ALTER TABLE pool_metadata_v2 RENAME TO pool_metadata")

                if prices_need_migration:
                    self._create_pool_daily_prices_table("pool_daily_prices_v2")
                    self.con.execute(
                        """
                        INSERT INTO pool_daily_prices_v2 (
                            deployment, account_index, date, share_price, tvl,
                            daily_return, annual_percentage_yield, total_shares,
                            cumulative_pool_inflow, cumulative_pool_outflow,
                            written_at, cumulative_account_inflow,
                            cumulative_account_outflow, cumulative_spot_inflow,
                            cumulative_spot_outflow, cumulative_staking_inflow,
                            cumulative_staking_outflow, trade_pnl, trade_spot_pnl,
                            pool_pnl, staking_pnl, volume
                        )
                        SELECT ?, account_index, date, share_price, tvl,
                            daily_return, annual_percentage_yield, total_shares,
                            cumulative_pool_inflow, cumulative_pool_outflow,
                            written_at, cumulative_account_inflow,
                            cumulative_account_outflow, cumulative_spot_inflow,
                            cumulative_spot_outflow, cumulative_staking_inflow,
                            cumulative_staking_outflow, trade_pnl, trade_spot_pnl,
                            pool_pnl, staking_pnl, volume
                        FROM pool_daily_prices
                        """,
                        [LIGHTER_ETHEREUM.slug],
                    )
                    self.con.execute("DROP TABLE pool_daily_prices")
                    self.con.execute("ALTER TABLE pool_daily_prices_v2 RENAME TO pool_daily_prices")
            except duckdb.Error:
                self.con.execute("ROLLBACK")
                raise
            else:
                self.con.execute("COMMIT")

        if not self._table_exists("pool_snapshots"):
            self._create_pool_snapshots_table("pool_snapshots")

    def _table_exists(self, table_name: str) -> bool:
        """Check whether a DuckDB table exists.

        :param table_name:
            Unquoted internal table name.
        :return:
            ``True`` when the table exists.
        """
        row = self.con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
            [table_name],
        ).fetchone()
        return bool(row and row[0])

    def _get_table_columns(self, table_name: str) -> set[str]:
        """Read column names for an internal DuckDB table.

        :param table_name:
            Unquoted internal table name.
        :return:
            Column-name set.
        """
        return {row[1] for row in self.con.execute(f"PRAGMA table_info('{table_name}')").fetchall()}

    def _ensure_columns(self, table_name: str, columns: dict[str, str]) -> None:
        """Add missing nullable source columns to an existing table.

        :param table_name:
            Trusted internal table name.
        :param columns:
            Mapping of column names to trusted DuckDB type declarations.
        """
        existing = self._get_table_columns(table_name)
        for column_name, type_sql in columns.items():
            if column_name not in existing:
                self.con.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {type_sql}")

    def _create_pool_metadata_table(self, table_name: str) -> None:
        """Create a deployment-aware pool metadata table.

        :param table_name:
            Trusted internal table name used during schema migration.
        """
        self.con.execute(f"""
            CREATE TABLE {table_name} (
                deployment VARCHAR NOT NULL,
                account_index BIGINT NOT NULL,
                name VARCHAR NOT NULL,
                description VARCHAR,
                l1_address VARCHAR,
                is_llp BOOLEAN DEFAULT FALSE,
                status INTEGER DEFAULT 0,
                operator_fee DOUBLE,
                total_asset_value DOUBLE,
                annual_percentage_yield DOUBLE,
                sharpe_ratio DOUBLE,
                total_shares BIGINT,
                operator_shares BIGINT,
                created_at TIMESTAMP,
                last_updated TIMESTAMP NOT NULL,
                PRIMARY KEY (deployment, account_index)
            )
        """)

    def _create_pool_daily_prices_table(self, table_name: str) -> None:
        """Create a deployment-aware daily price table.

        :param table_name:
            Trusted internal table name used during schema migration.
        """
        self.con.execute(f"""
            CREATE TABLE {table_name} (
                deployment VARCHAR NOT NULL,
                account_index BIGINT NOT NULL,
                date DATE NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE,
                daily_return DOUBLE,
                annual_percentage_yield DOUBLE,
                total_shares BIGINT,
                cumulative_pool_inflow DOUBLE,
                cumulative_pool_outflow DOUBLE,
                written_at TIMESTAMP,
                cumulative_account_inflow DOUBLE,
                cumulative_account_outflow DOUBLE,
                cumulative_spot_inflow DOUBLE,
                cumulative_spot_outflow DOUBLE,
                cumulative_staking_inflow DOUBLE,
                cumulative_staking_outflow DOUBLE,
                trade_pnl DOUBLE,
                trade_spot_pnl DOUBLE,
                pool_pnl DOUBLE,
                staking_pnl DOUBLE,
                volume DOUBLE,
                PRIMARY KEY (deployment, account_index, date)
            )
        """)

    def _create_pool_snapshots_table(self, table_name: str) -> None:
        """Create a deployment-aware point-in-time snapshot table.

        :param table_name:
            Trusted internal table name.
        """
        self.con.execute(f"""
            CREATE TABLE {table_name} (
                deployment VARCHAR NOT NULL,
                snapshot_timestamp TIMESTAMP NOT NULL,
                account_index BIGINT NOT NULL,
                account_status INTEGER,
                pool_status INTEGER,
                account_type INTEGER,
                account_trading_mode INTEGER,
                total_asset_value DOUBLE,
                cross_asset_value DOUBLE,
                collateral DOUBLE,
                available_balance DOUBLE,
                initial_margin_requirement DOUBLE,
                maintenance_margin_requirement DOUBLE,
                operator_fee DOUBLE,
                min_operator_share_rate DOUBLE,
                annual_percentage_yield DOUBLE,
                sharpe_ratio DOUBLE,
                total_shares BIGINT,
                operator_shares BIGINT,
                operator_share_fraction DOUBLE,
                pending_order_count INTEGER,
                total_order_count BIGINT,
                total_isolated_order_count BIGINT,
                transaction_time BIGINT,
                position_count INTEGER,
                gross_position_value DOUBLE,
                net_position_value DOUBLE,
                long_position_value DOUBLE,
                short_position_value DOUBLE,
                top_position_fraction DOUBLE,
                allocated_margin DOUBLE,
                unrealised_pnl DOUBLE,
                realised_pnl DOUBLE,
                funding_paid_out DOUBLE,
                open_order_count INTEGER,
                asset_count INTEGER,
                strategy_count INTEGER,
                strategy_collateral DOUBLE,
                pending_unlock_count INTEGER,
                source_account_json VARCHAR NOT NULL,
                PRIMARY KEY (deployment, snapshot_timestamp, account_index)
            )
        """)

    def upsert_pool_metadata(
        self,
        account_index: int,
        name: str,
        deployment: str = LIGHTER_ETHEREUM.slug,
        description: str | None = None,
        l1_address: str | None = None,
        is_llp: bool = False,
        status: int = 0,
        operator_fee: float | None = None,
        total_asset_value: float | None = None,
        annual_percentage_yield: float | None = None,
        sharpe_ratio: float | None = None,
        total_shares: int | None = None,
        operator_shares: int | None = None,
        created_at: datetime.datetime | None = None,
    ):
        """Insert or update pool metadata.

        :param deployment:
            Stable deployment slug.
        :param account_index:
            Pool account index, unique within the deployment.
        :param name:
            Pool display name.
        :param description:
            Pool description text.
        :param l1_address:
            Operator address reported in the API's legacy ``l1_address`` field.
        :param is_llp:
            Whether this is the LLP protocol pool.
        :param status:
            Pool status code from the API (0 = active).
        :param operator_fee:
            Operator fee percentage.
        :param total_asset_value:
            Total value locked in the deployment's collateral currency.
        :param annual_percentage_yield:
            Current APY.
        :param sharpe_ratio:
            Risk-adjusted return metric.
        :param total_shares:
            Current outstanding pool shares.
        :param operator_shares:
            Current shares owned by the pool operator.
        :param created_at:
            Pool creation timestamp.
        """
        self.con.execute(
            """
            INSERT INTO pool_metadata (
                deployment, account_index, name, description, l1_address, is_llp,
                status, operator_fee, total_asset_value, annual_percentage_yield,
                sharpe_ratio, total_shares, operator_shares, created_at, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (deployment, account_index) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                l1_address = excluded.l1_address,
                is_llp = excluded.is_llp,
                status = excluded.status,
                operator_fee = excluded.operator_fee,
                total_asset_value = excluded.total_asset_value,
                annual_percentage_yield = excluded.annual_percentage_yield,
                sharpe_ratio = excluded.sharpe_ratio,
                total_shares = excluded.total_shares,
                operator_shares = excluded.operator_shares,
                created_at = excluded.created_at,
                last_updated = excluded.last_updated
            """,
            [
                deployment,
                account_index,
                name,
                description,
                l1_address,
                is_llp,
                status,
                operator_fee,
                total_asset_value,
                annual_percentage_yield,
                sharpe_ratio,
                total_shares,
                operator_shares,
                created_at,
                native_datetime_utc_now(),
            ],
        )

    def upsert_daily_prices(
        self,
        rows: list[LighterDailyPriceRow | tuple[object, ...]],
        deployment: str = LIGHTER_ETHEREUM.slug,
        cutoff_date: datetime.date | None = None,
    ) -> None:
        """Bulk upsert daily price rows for a pool.

        :param deployment:
            Stable deployment slug applied to every row.
        :param rows:
            Daily price rows with source share and cumulative flow counters.
        :param cutoff_date:
            If provided, only store rows up to this date (inclusive).
            Used for incremental scanning / testing.
        """
        if cutoff_date is not None:
            rows = [row for row in rows if (row.date if isinstance(row, LighterDailyPriceRow) else row[1]) <= cutoff_date]

        if not rows:
            return

        normalised_rows = [_normalise_daily_price_row(row) for row in rows]
        self.con.executemany(
            """
            INSERT INTO pool_daily_prices (
                deployment, account_index, date, share_price, tvl,
                daily_return, annual_percentage_yield, total_shares,
                cumulative_pool_inflow, cumulative_pool_outflow, written_at,
                cumulative_account_inflow, cumulative_account_outflow,
                cumulative_spot_inflow, cumulative_spot_outflow,
                cumulative_staking_inflow, cumulative_staking_outflow,
                trade_pnl, trade_spot_pnl, pool_pnl, staking_pnl, volume
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (deployment, account_index, date) DO UPDATE SET
                share_price = excluded.share_price,
                tvl = excluded.tvl,
                daily_return = excluded.daily_return,
                annual_percentage_yield = excluded.annual_percentage_yield,
                total_shares = COALESCE(excluded.total_shares, pool_daily_prices.total_shares),
                cumulative_pool_inflow = COALESCE(excluded.cumulative_pool_inflow, pool_daily_prices.cumulative_pool_inflow),
                cumulative_pool_outflow = COALESCE(excluded.cumulative_pool_outflow, pool_daily_prices.cumulative_pool_outflow),
                written_at = excluded.written_at,
                cumulative_account_inflow = COALESCE(excluded.cumulative_account_inflow, pool_daily_prices.cumulative_account_inflow),
                cumulative_account_outflow = COALESCE(excluded.cumulative_account_outflow, pool_daily_prices.cumulative_account_outflow),
                cumulative_spot_inflow = COALESCE(excluded.cumulative_spot_inflow, pool_daily_prices.cumulative_spot_inflow),
                cumulative_spot_outflow = COALESCE(excluded.cumulative_spot_outflow, pool_daily_prices.cumulative_spot_outflow),
                cumulative_staking_inflow = COALESCE(excluded.cumulative_staking_inflow, pool_daily_prices.cumulative_staking_inflow),
                cumulative_staking_outflow = COALESCE(excluded.cumulative_staking_outflow, pool_daily_prices.cumulative_staking_outflow),
                trade_pnl = COALESCE(excluded.trade_pnl, pool_daily_prices.trade_pnl),
                trade_spot_pnl = COALESCE(excluded.trade_spot_pnl, pool_daily_prices.trade_spot_pnl),
                pool_pnl = COALESCE(excluded.pool_pnl, pool_daily_prices.pool_pnl),
                staking_pnl = COALESCE(excluded.staking_pnl, pool_daily_prices.staking_pnl),
                volume = COALESCE(excluded.volume, pool_daily_prices.volume)
            """,
            [(deployment, *row) for row in normalised_rows],
        )

    def insert_pool_snapshot(
        self,
        snapshot: LighterPoolSnapshot,
        deployment: str = LIGHTER_ETHEREUM.slug,
    ) -> None:
        """Insert one append-only Lighter pool snapshot.

        Stores queryable selection and risk metrics plus a complete JSON copy
        of the current account state. Historical price/return arrays are
        removed from that JSON because the daily table already stores them.
        The method does not backfill older dates: snapshots exist only from the
        time this collector starts, and earlier history remains missing/``NaN``
        in downstream joins.

        :param snapshot:
            Current account and pool state parsed from one public API response.
        :param deployment:
            Stable deployment slug. Defaults to Ethereum for compatibility.
        """
        self.con.execute(
            """
            INSERT INTO pool_snapshots (
                deployment, snapshot_timestamp, account_index, account_status, pool_status,
                account_type, account_trading_mode,
                total_asset_value, cross_asset_value, collateral, available_balance,
                initial_margin_requirement, maintenance_margin_requirement,
                operator_fee, min_operator_share_rate, annual_percentage_yield,
                sharpe_ratio, total_shares, operator_shares, operator_share_fraction,
                pending_order_count, total_order_count, total_isolated_order_count,
                transaction_time, position_count,
                gross_position_value, net_position_value, long_position_value,
                short_position_value, top_position_fraction, allocated_margin,
                unrealised_pnl, realised_pnl, funding_paid_out, open_order_count,
                asset_count, strategy_count, strategy_collateral, pending_unlock_count,
                source_account_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (deployment, snapshot_timestamp, account_index) DO UPDATE SET
                account_status = excluded.account_status,
                pool_status = excluded.pool_status,
                account_type = excluded.account_type,
                account_trading_mode = excluded.account_trading_mode,
                total_asset_value = excluded.total_asset_value,
                cross_asset_value = excluded.cross_asset_value,
                collateral = excluded.collateral,
                available_balance = excluded.available_balance,
                initial_margin_requirement = excluded.initial_margin_requirement,
                maintenance_margin_requirement = excluded.maintenance_margin_requirement,
                operator_fee = excluded.operator_fee,
                min_operator_share_rate = excluded.min_operator_share_rate,
                annual_percentage_yield = excluded.annual_percentage_yield,
                sharpe_ratio = excluded.sharpe_ratio,
                total_shares = excluded.total_shares,
                operator_shares = excluded.operator_shares,
                operator_share_fraction = excluded.operator_share_fraction,
                pending_order_count = excluded.pending_order_count,
                total_order_count = excluded.total_order_count,
                total_isolated_order_count = excluded.total_isolated_order_count,
                transaction_time = excluded.transaction_time,
                position_count = excluded.position_count,
                gross_position_value = excluded.gross_position_value,
                net_position_value = excluded.net_position_value,
                long_position_value = excluded.long_position_value,
                short_position_value = excluded.short_position_value,
                top_position_fraction = excluded.top_position_fraction,
                allocated_margin = excluded.allocated_margin,
                unrealised_pnl = excluded.unrealised_pnl,
                realised_pnl = excluded.realised_pnl,
                funding_paid_out = excluded.funding_paid_out,
                open_order_count = excluded.open_order_count,
                asset_count = excluded.asset_count,
                strategy_count = excluded.strategy_count,
                strategy_collateral = excluded.strategy_collateral,
                pending_unlock_count = excluded.pending_unlock_count,
                source_account_json = excluded.source_account_json
            """,
            [
                deployment,
                snapshot.snapshot_timestamp,
                snapshot.account_index,
                snapshot.account_status,
                snapshot.pool_status,
                snapshot.account_type,
                snapshot.account_trading_mode,
                snapshot.total_asset_value,
                snapshot.cross_asset_value,
                snapshot.collateral,
                snapshot.available_balance,
                snapshot.initial_margin_requirement,
                snapshot.maintenance_margin_requirement,
                snapshot.operator_fee,
                snapshot.min_operator_share_rate,
                snapshot.annual_percentage_yield,
                snapshot.sharpe_ratio,
                snapshot.total_shares,
                snapshot.operator_shares,
                snapshot.operator_share_fraction,
                snapshot.pending_order_count,
                snapshot.total_order_count,
                snapshot.total_isolated_order_count,
                snapshot.transaction_time,
                snapshot.position_count,
                snapshot.gross_position_value,
                snapshot.net_position_value,
                snapshot.long_position_value,
                snapshot.short_position_value,
                snapshot.top_position_fraction,
                snapshot.allocated_margin,
                snapshot.unrealised_pnl,
                snapshot.realised_pnl,
                snapshot.funding_paid_out,
                snapshot.open_order_count,
                snapshot.asset_count,
                snapshot.strategy_count,
                snapshot.strategy_collateral,
                snapshot.pending_unlock_count,
                json.dumps(snapshot.source_account),
            ],
        )

    def get_pool_snapshot_history(
        self,
        account_index: int,
        deployment: str = LIGHTER_ETHEREUM.slug,
    ) -> pd.DataFrame:
        """Retrieve point-in-time snapshots for one Lighter pool.

        :param account_index:
            Lighter pool account index.
        :param deployment:
            Stable deployment slug. Defaults to Ethereum for compatibility.
        :return:
            Snapshot rows ordered from oldest to newest. The result starts at
            the collection start date; no earlier values are fabricated.
        """
        return self.con.execute(
            """
            SELECT *
            FROM pool_snapshots
            WHERE deployment = ? AND account_index = ?
            ORDER BY snapshot_timestamp
            """,
            [deployment, account_index],
        ).fetchdf()

    def get_latest_pool_snapshots(self, deployment: str | None = None) -> pd.DataFrame:
        """Retrieve the most recent point-in-time snapshot for every pool.

        :param deployment:
            Optional deployment slug filter.
        :return:
            Latest snapshot per account, ordered by current TVL.
        """
        where_clause = "" if deployment is None else "WHERE deployment = ?"
        parameters = [] if deployment is None else [deployment]
        return self.con.execute(
            f"""
                SELECT *
                FROM pool_snapshots
                {where_clause}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY deployment, account_index
                    ORDER BY snapshot_timestamp DESC
                ) = 1
                ORDER BY deployment, total_asset_value DESC NULLS LAST
            """,
            parameters,
        ).fetchdf()

    def get_all_daily_prices(self, deployment: str | None = None) -> pd.DataFrame:
        """Retrieve all daily price data.

        :param deployment:
            Optional deployment slug filter.
        :return:
            DataFrame with price, source-share, and cumulative-flow columns.
        """
        if deployment is None:
            return self.con.execute("SELECT * FROM pool_daily_prices ORDER BY deployment, account_index, date").fetchdf()
        return self.con.execute(
            "SELECT * FROM pool_daily_prices WHERE deployment = ? ORDER BY account_index, date",
            [deployment],
        ).fetchdf()

    def get_pool_daily_prices(self, account_index: int, deployment: str = LIGHTER_ETHEREUM.slug) -> pd.DataFrame:
        """Get daily prices for a specific pool.

        :param account_index:
            Pool account index.
        :param deployment:
            Stable deployment slug. Defaults to Ethereum for compatibility.
        :return:
            DataFrame with daily price data for the pool.
        """
        return self.con.execute(
            "SELECT * FROM pool_daily_prices WHERE deployment = ? AND account_index = ? ORDER BY date",
            [deployment, account_index],
        ).fetchdf()

    def get_all_pool_metadata(self, deployment: str | None = None) -> pd.DataFrame:
        """Retrieve all pool metadata ordered by TVL.

        :param deployment:
            Optional deployment slug filter.
        :return:
            DataFrame with pool metadata.
        """
        if deployment is None:
            return self.con.execute("SELECT * FROM pool_metadata ORDER BY deployment, total_asset_value DESC").fetchdf()
        return self.con.execute(
            "SELECT * FROM pool_metadata WHERE deployment = ? ORDER BY total_asset_value DESC",
            [deployment],
        ).fetchdf()

    def get_pool_count(self, deployment: str | None = None) -> int:
        """Get number of pools with daily price data.

        :param deployment:
            Optional deployment slug filter.
        :return:
            Count of unique pools.
        """
        if deployment is None:
            result = self.con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT deployment, account_index FROM pool_daily_prices)").fetchone()
        else:
            result = self.con.execute(
                "SELECT COUNT(DISTINCT account_index) FROM pool_daily_prices WHERE deployment = ?",
                [deployment],
            ).fetchone()
        return result[0] if result else 0

    def get_vault_count(self, deployment: str | None = None) -> int:
        """Get number of pools with daily price data.

        Alias for :py:meth:`get_pool_count` to unify the interface
        across Hyperliquid, GRVT, and Lighter scanners.

        :param deployment:
            Optional deployment slug filter.
        :return:
            Count of unique pools.
        """
        return self.get_pool_count(deployment=deployment)

    def get_pool_daily_price_count(self, account_index: int, deployment: str = LIGHTER_ETHEREUM.slug) -> int:
        """Get number of daily price records for a specific pool.

        :param account_index:
            Pool account index.
        :param deployment:
            Stable deployment slug. Defaults to Ethereum for compatibility.
        :return:
            Count of daily price records.
        """
        result = self.con.execute(
            "SELECT COUNT(*) FROM pool_daily_prices WHERE deployment = ? AND account_index = ?",
            [deployment, account_index],
        ).fetchone()
        return result[0] if result else 0

    def get_pool_last_date(self, account_index: int, deployment: str = LIGHTER_ETHEREUM.slug) -> datetime.date | None:
        """Get the latest date with price data for a pool.

        :param account_index:
            Pool account index.
        :param deployment:
            Stable deployment slug. Defaults to Ethereum for compatibility.
        :return:
            Latest date or ``None`` if no data.
        """
        result = self.con.execute(
            "SELECT MAX(date) FROM pool_daily_prices WHERE deployment = ? AND account_index = ?",
            [deployment, account_index],
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
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        logger.warning(
            "Failed to fetch pool details for %s (%d): %s",
            summary.name,
            summary.account_index,
            e,
        )
        return False

    # Fetch historical shares and source cumulative flow counters for TVL and
    # later flow export. A failure must not discard share-price ingestion.
    try:
        pnl_history_by_date = fetch_pool_daily_pnl_history(
            session,
            summary.account_index,
            timeout=timeout,
        )
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        logger.warning(
            "Failed to fetch PnL history for %s (%d), storing null source counters: %s",
            summary.name,
            summary.account_index,
            e,
        )
        pnl_history_by_date = None

    # Store current metadata and the point-in-time snapshot even when this
    # account does not yet have enough price history for a daily row.
    db.upsert_pool_metadata(
        deployment=session.deployment.slug,
        account_index=summary.account_index,
        name=detail.name or summary.name,
        description=detail.description or (LIGHTER_LLP_DESCRIPTION if summary.is_llp else ""),
        l1_address=summary.l1_address,
        is_llp=summary.is_llp,
        status=summary.status,
        operator_fee=detail.operator_fee,
        total_asset_value=summary.total_asset_value,
        annual_percentage_yield=detail.annual_percentage_yield,
        sharpe_ratio=detail.sharpe_ratio,
        total_shares=detail.total_shares,
        operator_shares=detail.operator_shares,
        created_at=summary.created_at,
    )
    db.insert_pool_snapshot(
        detail.snapshot,
        deployment=session.deployment.slug,
    )

    daily_df = pool_detail_to_daily_dataframe(detail, pnl_history_by_date=pnl_history_by_date)
    if daily_df.empty:
        logger.debug(
            "Skipping daily prices for pool %s (%d): empty share price history",
            summary.name,
            summary.account_index,
        )
        return False

    # Build daily price rows
    written_at = native_datetime_utc_now()
    rows: list[LighterDailyPriceRow] = []
    for date_val, row_data in daily_df.iterrows():
        rows.append(
            LighterDailyPriceRow(
                account_index=summary.account_index,
                date=date_val,
                share_price=float(row_data["share_price"]),
                tvl=float(row_data["tvl"]),
                daily_return=float(row_data["daily_return"]),
                annual_percentage_yield=summary.annual_percentage_yield,
                total_shares=int(row_data["total_shares"]) if pd.notna(row_data["total_shares"]) else None,
                cumulative_pool_inflow=_optional_dataframe_float(row_data.get("cumulative_pool_inflow")),
                cumulative_pool_outflow=_optional_dataframe_float(row_data.get("cumulative_pool_outflow")),
                written_at=written_at,
                cumulative_account_inflow=_optional_dataframe_float(row_data.get("cumulative_account_inflow")),
                cumulative_account_outflow=_optional_dataframe_float(row_data.get("cumulative_account_outflow")),
                cumulative_spot_inflow=_optional_dataframe_float(row_data.get("cumulative_spot_inflow")),
                cumulative_spot_outflow=_optional_dataframe_float(row_data.get("cumulative_spot_outflow")),
                cumulative_staking_inflow=_optional_dataframe_float(row_data.get("cumulative_staking_inflow")),
                cumulative_staking_outflow=_optional_dataframe_float(row_data.get("cumulative_staking_outflow")),
                trade_pnl=_optional_dataframe_float(row_data.get("trade_pnl")),
                trade_spot_pnl=_optional_dataframe_float(row_data.get("trade_spot_pnl")),
                pool_pnl=_optional_dataframe_float(row_data.get("pool_pnl")),
                staking_pnl=_optional_dataframe_float(row_data.get("staking_pnl")),
                volume=_optional_dataframe_float(row_data.get("volume")),
            )
        )

    db.upsert_daily_prices(
        deployment=session.deployment.slug,
        rows=rows,
        cutoff_date=cutoff_date,
    )

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
    3. Fetches per-pool details, daily PnL, and share-price history in parallel
    4. Stores daily history and an append-only current-state snapshot in DuckDB

    :param session:
        HTTP session with rate limiting.
    :param db_path:
        Path to the DuckDB database file.
    :param min_tvl:
        Minimum TVL in the deployment's collateral currency to include a pool.
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

    logger.info("Fetched %d pools from %s", len(all_pools), session.deployment.name)

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
    desc = f"Fetching {session.deployment.name} pool details"
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_process_pool_worker)(session, db, summary, cutoff_date, timeout) for summary in tqdm(filtered, desc=desc))

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)

    db.save()

    logger.info(
        "%s daily scan complete. Processed %d pools (%d successful, %d failed) into %s",
        session.deployment.name,
        len(filtered),
        success_count,
        fail_count,
        db_path,
    )

    return db
