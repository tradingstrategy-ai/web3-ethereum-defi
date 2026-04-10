"""High-frequency Hyperliquid vault metrics pipeline.

Collects vault share prices, PnL, TVL and flow data at configurable
sub-daily intervals (default 4 h, configurable down to 1 h).  Uses
Webshare rotating proxies for parallel throughput when available.

Mirrors the architecture of :py:mod:`~eth_defi.hyperliquid.daily_metrics`
but stores rows keyed by ``(vault_address, timestamp)`` instead of
``(vault_address, date)``.

Key differences from the daily pipeline:

- **Raw timestamps**: rows preserve the API's original timestamps
  (no ``.date()`` truncation or bucket flooring).  The API returns
  data at varying resolution (~weekly for ``allTime``, sub-daily
  for ``day`` period) — all points are stored as-is.
- **Proxy-aware session pool**: workers get pre-cloned sessions for
  independent rate limiting per proxy IP.
- **Resumable with overlap**: stores rows ``>=`` the last stored
  timestamp so the latest row is always refreshed via idempotent
  upsert.
"""

import datetime
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.constants import (
    HYPERLIQUID_HIGH_FREQ_DEFAULT_INTERVAL,
    HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE,
)
from eth_defi.hyperliquid.vault_metrics_db import HyperliquidMetricsDatabaseBase
from eth_defi.hyperliquid.daily_metrics import (
    portfolio_to_combined_dataframe,
)
from eth_defi.hyperliquid.deposit import (
    aggregate_daily_flows,
    fetch_vault_deposits,
)
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.vault import (
    HyperliquidVault,
    VaultInfo,
    VaultSummary,
    fetch_all_vaults,
)
from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Row dataclass
# ──────────────────────────────────────────────


@dataclass(slots=True)
class HyperliquidHighFreqPriceRow:
    """A single high-frequency price row ready for DuckDB upsert."""

    vault_address: HexAddress
    #: Raw API timestamp (naive UTC, no flooring or normalisation).
    timestamp: datetime.datetime
    share_price: float
    tvl: float
    cumulative_pnl: float
    cumulative_volume: float | None = None
    daily_pnl: float = 0.0
    daily_return: float = 0.0
    follower_count: int | None = None
    apr: float | None = None
    is_closed: bool | None = None
    allow_deposits: bool | None = None
    leader_fraction: float | None = None
    leader_commission: float | None = None
    #: Flow fields use bucket-relative names (not "daily_" prefix).
    deposit_count: int | None = None
    withdrawal_count: int | None = None
    deposit_usd: float | None = None
    withdrawal_usd: float | None = None
    epoch_reset: bool | None = None
    data_source: str = "api"
    #: When this row was actually written/fetched (naive UTC).
    written_at: datetime.datetime | None = None

    def __post_init__(self) -> None:
        """Normalise vault address casing for database writes."""
        self.vault_address = self.vault_address.lower()

    def as_db_tuple(self) -> tuple[object, ...]:
        """Convert to the 21-column DuckDB layout."""
        return (
            self.vault_address,
            self.timestamp,
            self.share_price,
            self.tvl,
            self.cumulative_pnl,
            self.cumulative_volume,
            self.daily_pnl,
            self.daily_return,
            self.follower_count,
            self.apr,
            self.is_closed,
            self.allow_deposits,
            self.leader_fraction,
            self.leader_commission,
            self.deposit_count,
            self.withdrawal_count,
            self.deposit_usd,
            self.withdrawal_usd,
            self.epoch_reset,
            self.data_source,
            self.written_at,
        )


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────


class HyperliquidHighFreqMetricsDatabase(HyperliquidMetricsDatabaseBase):
    """DuckDB database for high-frequency Hyperliquid vault metrics.

    Inherits shared metadata and lifecycle methods from
    :py:class:`~eth_defi.hyperliquid.vault_metrics_db.HyperliquidMetricsDatabaseBase`.
    Uses ``TIMESTAMP`` primary key instead of ``DATE``.
    """

    price_table = "vault_high_freq_prices"
    time_column = "timestamp"

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def _init_price_schema(self):
        """Create the HF price table."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_high_freq_prices (
                vault_address VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE NOT NULL,
                cumulative_pnl DOUBLE,
                cumulative_volume DOUBLE,
                daily_pnl DOUBLE,
                daily_return DOUBLE,
                follower_count INTEGER,
                apr DOUBLE,
                is_closed BOOLEAN,
                allow_deposits BOOLEAN,
                leader_fraction DOUBLE,
                leader_commission DOUBLE,
                deposit_count INTEGER,
                withdrawal_count INTEGER,
                deposit_usd DOUBLE,
                withdrawal_usd DOUBLE,
                epoch_reset BOOLEAN,
                data_source VARCHAR,
                written_at TIMESTAMP,
                PRIMARY KEY (vault_address, timestamp)
            )
        """)

    # ── Price data methods (HF-specific) ──

    def upsert_high_freq_prices(
        self,
        rows: list[HyperliquidHighFreqPriceRow],
        cutoff_timestamp: datetime.datetime | None = None,
    ):
        """Bulk upsert high-frequency price rows.

        Uses COALESCE for sparse/stateful columns (matching
        ``daily_metrics.py:804`` pattern) so that overlap re-upserts
        and tombstone rows do not wipe existing values.

        :param rows:
            Price rows to upsert.
        :param cutoff_timestamp:
            If provided, only store rows up to this timestamp (inclusive).
        """
        if cutoff_timestamp is not None:
            rows = [r for r in rows if r.timestamp <= cutoff_timestamp]

        if not rows:
            return

        db_rows = [r.as_db_tuple() for r in rows]

        # Thread safety: use a per-call cursor so concurrent worker
        # threads do not clobber each other's result sets on the
        # shared connection.  See ``HyperliquidMetricsDatabaseBase``.
        self.con.cursor().executemany(
            """
            INSERT INTO vault_high_freq_prices (
                vault_address, timestamp, share_price, tvl, cumulative_pnl,
                cumulative_volume, daily_pnl, daily_return, follower_count, apr,
                is_closed, allow_deposits, leader_fraction, leader_commission,
                deposit_count, withdrawal_count,
                deposit_usd, withdrawal_usd, epoch_reset,
                data_source, written_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (vault_address, timestamp)
            DO UPDATE SET
                share_price = EXCLUDED.share_price,
                tvl = EXCLUDED.tvl,
                cumulative_pnl = EXCLUDED.cumulative_pnl,
                cumulative_volume = COALESCE(EXCLUDED.cumulative_volume, vault_high_freq_prices.cumulative_volume),
                daily_pnl = EXCLUDED.daily_pnl,
                daily_return = EXCLUDED.daily_return,
                follower_count = COALESCE(EXCLUDED.follower_count, vault_high_freq_prices.follower_count),
                apr = COALESCE(EXCLUDED.apr, vault_high_freq_prices.apr),
                is_closed = COALESCE(EXCLUDED.is_closed, vault_high_freq_prices.is_closed),
                allow_deposits = COALESCE(EXCLUDED.allow_deposits, vault_high_freq_prices.allow_deposits),
                leader_fraction = COALESCE(EXCLUDED.leader_fraction, vault_high_freq_prices.leader_fraction),
                leader_commission = COALESCE(EXCLUDED.leader_commission, vault_high_freq_prices.leader_commission),
                deposit_count = COALESCE(EXCLUDED.deposit_count, vault_high_freq_prices.deposit_count),
                withdrawal_count = COALESCE(EXCLUDED.withdrawal_count, vault_high_freq_prices.withdrawal_count),
                deposit_usd = COALESCE(EXCLUDED.deposit_usd, vault_high_freq_prices.deposit_usd),
                withdrawal_usd = COALESCE(EXCLUDED.withdrawal_usd, vault_high_freq_prices.withdrawal_usd),
                epoch_reset = COALESCE(EXCLUDED.epoch_reset, vault_high_freq_prices.epoch_reset),
                data_source = COALESCE(EXCLUDED.data_source, vault_high_freq_prices.data_source),
                written_at = EXCLUDED.written_at
            """,
            db_rows,
        )

    def get_all_high_freq_prices(self) -> pd.DataFrame:
        """Get all high-frequency price data across all vaults."""
        return (
            self.con.cursor()
            .execute("""
            SELECT * FROM vault_high_freq_prices
            ORDER BY vault_address, timestamp
        """)
            .df()
        )

    def get_vault_high_freq_prices(self, vault_address: HexAddress) -> pd.DataFrame:
        """Get high-frequency price data for a specific vault."""
        return (
            self.con.cursor()
            .execute(
                """
            SELECT * FROM vault_high_freq_prices
            WHERE vault_address = ?
            ORDER BY timestamp
            """,
                [vault_address.lower()],
            )
            .df()
        )

    def get_vault_last_timestamp(self, vault_address: HexAddress) -> datetime.datetime | None:
        """Get the latest stored timestamp for a vault.

        :return:
            The most recent timestamp, or ``None`` if no data exists.
        """
        row = (
            self.con.cursor()
            .execute(
                "SELECT MAX(timestamp) FROM vault_high_freq_prices WHERE vault_address = ?",
                [vault_address.lower()],
            )
            .fetchone()
        )
        return row[0] if row and row[0] is not None else None

    def _write_tombstone_rows(self, vault_addresses: list[str]) -> int:
        """Write tombstone HF price rows for the given vaults."""
        if not vault_addresses:
            return 0

        now = native_datetime_utc_now()
        tombstone_rows = []

        for addr in vault_addresses:
            last_row = self._get_last_price_row(addr)
            if last_row is None:
                continue

            share_price, cumulative_pnl = last_row
            tombstone_rows.append(
                HyperliquidHighFreqPriceRow(
                    vault_address=addr,
                    timestamp=now,
                    share_price=share_price,
                    tvl=0.0,
                    cumulative_pnl=cumulative_pnl,
                    daily_pnl=0.0,
                    daily_return=0.0,
                    follower_count=0,
                    data_source="tombstone",
                    written_at=now,
                )
            )

        if tombstone_rows:
            self.upsert_high_freq_prices(tombstone_rows)

        return len(tombstone_rows)


# ──────────────────────────────────────────────
# Per-vault fetch and store
# ──────────────────────────────────────────────


def fetch_and_store_vault_high_freq(
    session: HyperliquidSession,
    db: HyperliquidHighFreqMetricsDatabase,
    summary: VaultSummary,
    scan_interval: datetime.timedelta = HYPERLIQUID_HIGH_FREQ_DEFAULT_INTERVAL,
    cutoff_timestamp: datetime.datetime | None = None,
    flow_backfill_days: int = 7,
    timeout: float = 30.0,
) -> bool:
    """Fetch a single vault's details and store HF metrics.

    1. Fetch vault info via ``vaultDetails`` API
    2. Compute share prices via ``portfolio_to_combined_dataframe()``
    3. Store raw API timestamps (no normalisation or flooring)
    4. Fetch deposit/withdrawal events and aggregate by day
    5. Store rows ``>=`` last stored timestamp (resumable with overlap)

    Unlike the daily pipeline which truncates to ``.date()``, the HF
    pipeline preserves the raw timestamps from the merged portfolio
    history.  The API returns data at varying resolution (weekly for
    ``allTime``, sub-daily for ``day`` period) — all points are stored
    as-is.  Flow data is naturally daily and matched via ``.date()``
    on the raw timestamp.

    :param session:
        HTTP session (should be a worker clone with proxy).
    :param db:
        High-frequency metrics database.
    :param summary:
        Vault summary from bulk listing.
    :param scan_interval:
        Not used for timestamp normalisation (kept for API
        compatibility).  May be used for future sub-daily flow
        bucketing.
    :param cutoff_timestamp:
        If set, discard rows with timestamp > cutoff.
    :param flow_backfill_days:
        Days to backfill flow data. Set to 0 to disable.
    :param timeout:
        HTTP request timeout.
    :return:
        True if vault was successfully processed.
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
        logger.warning(
            "Failed to fetch vault details for %s (%s): %s",
            summary.name,
            vault_address,
            e,
        )
        return False

    # Get portfolio history
    portfolio_dict = info.portfolio
    all_time = portfolio_dict.get("allTime") if portfolio_dict else None
    if all_time is None or len(all_time.account_value_history) < 2:
        logger.debug(
            "Skipping vault %s (%s): insufficient portfolio history",
            summary.name,
            vault_address,
        )
        return False

    # Compute share prices (reused from daily pipeline)
    combined_df = portfolio_to_combined_dataframe(portfolio=portfolio_dict)
    if combined_df.empty:
        logger.debug(
            "Skipping vault %s (%s): empty combined DataFrame",
            summary.name,
            vault_address,
        )
        return False

    # Fetch deposit/withdrawal events for flow metrics.
    # Flows are naturally daily and aggregated by calendar date,
    # then matched to price rows via ts.date().
    daily_flows: dict[datetime.date, tuple[int, int, float, float]] = {}
    flow_start_date: datetime.date | None = None

    if flow_backfill_days > 0:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        flow_start_date = today - datetime.timedelta(days=flow_backfill_days)
        flow_start_dt = datetime.datetime(
            flow_start_date.year,
            flow_start_date.month,
            flow_start_date.day,
        )
        flow_end_dt = datetime.datetime(
            yesterday.year,
            yesterday.month,
            yesterday.day,
            23,
            59,
            59,
        )

        try:
            events = list(
                fetch_vault_deposits(
                    session,
                    vault_address,
                    start_time=flow_start_dt,
                    end_time=flow_end_dt,
                    timeout=timeout,
                )
            )
            daily_flows = aggregate_daily_flows(events)
            logger.debug(
                "Fetched %d deposit events for %s (%s), %d days with activity",
                len(events),
                summary.name,
                vault_address,
                len(daily_flows),
            )
        except Exception as e:
            logger.warning(
                "Failed to fetch deposit events for %s (%s): %s",
                summary.name,
                vault_address,
                e,
            )

    # Store metadata
    follower_count = len(info.followers)
    commission_rate = float(info.commission_rate) if info.commission_rate is not None else None
    leader_fraction = float(info.leader_fraction) if info.leader_fraction is not None else None
    leader_commission = float(info.leader_commission) if info.leader_commission is not None else None
    apr_val = float(summary.apr) if summary.apr is not None else None
    cumulative_volume = float(all_time.volume) if all_time.volume is not None else None

    flow_data_earliest_date = flow_start_date

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
        allow_deposits=info.allow_deposits,
        flow_data_earliest_date=flow_data_earliest_date,
    )

    # Build HF price rows using raw API timestamps (no normalisation).
    # The merged portfolio history already has the highest available
    # resolution from _merge_portfolio_periods().
    last_stored_ts = db.get_vault_last_timestamp(vault_address)

    # Precompute the last row index per calendar date so that flow
    # data is only attached once per day (avoiding duplication when
    # multiple intraday rows share the same .date()).
    last_row_per_date: dict[datetime.date, int] = {}
    for i, ts in enumerate(combined_df.index):
        raw_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        last_row_per_date[raw_ts.date()] = i

    last_idx = len(combined_df) - 1
    rows: list[HyperliquidHighFreqPriceRow] = []
    now = native_datetime_utc_now()
    prev_share_price = None

    for i, (ts, row_data) in enumerate(zip(combined_df.index, combined_df.itertuples())):
        raw_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts

        # Resumable: only store rows >= last stored timestamp
        if last_stored_ts is not None and raw_ts < last_stored_ts:
            # Still track prev_share_price for return calc
            prev_share_price = row_data.share_price
            continue

        # Cutoff filtering
        if cutoff_timestamp is not None and raw_ts > cutoff_timestamp:
            continue

        share_price = row_data.share_price
        tvl = row_data.total_assets
        cumulative_pnl_val = row_data.cumulative_pnl
        daily_pnl = row_data.pnl_update
        epoch_reset_val = bool(row_data.epoch_reset) if hasattr(row_data, "epoch_reset") else False

        if prev_share_price is not None and prev_share_price > 0:
            daily_return = (share_price - prev_share_price) / prev_share_price
        else:
            daily_return = 0.0

        prev_share_price = share_price

        # Only the latest row gets snapshot fields
        if i == last_idx:
            row_follower_count = follower_count
            row_apr = apr_val
            row_is_closed = info.is_closed
            row_allow_deposits = info.allow_deposits
            row_leader_fraction = leader_fraction
            row_leader_commission = leader_commission
            row_cumulative_volume = cumulative_volume
        else:
            row_follower_count = None
            row_apr = None
            row_is_closed = None
            row_allow_deposits = None
            row_leader_fraction = None
            row_leader_commission = None
            row_cumulative_volume = None

        # Flow data: only attach to the LAST row per calendar date to
        # avoid duplicating daily flow values across multiple intraday
        # rows.  Downstream netflow code sums these columns across rows,
        # so duplicates would overstate deposit/withdrawal counts and USD.
        date_val = raw_ts.date()
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        is_last_row_for_date = last_row_per_date.get(date_val) == i
        if is_last_row_for_date and flow_start_date is not None and flow_start_date <= date_val <= yesterday:
            flow = daily_flows.get(date_val, (0, 0, 0.0, 0.0))
            dep_count, wd_count, dep_usd, wd_usd = flow
        else:
            dep_count = None
            wd_count = None
            dep_usd = None
            wd_usd = None

        rows.append(
            HyperliquidHighFreqPriceRow(
                vault_address=vault_address,
                timestamp=raw_ts,
                share_price=share_price,
                tvl=tvl,
                cumulative_pnl=cumulative_pnl_val,
                cumulative_volume=row_cumulative_volume,
                daily_pnl=daily_pnl,
                daily_return=daily_return,
                follower_count=row_follower_count,
                apr=row_apr,
                is_closed=row_is_closed,
                allow_deposits=row_allow_deposits,
                leader_fraction=row_leader_fraction,
                leader_commission=row_leader_commission,
                deposit_count=dep_count,
                withdrawal_count=wd_count,
                deposit_usd=dep_usd,
                withdrawal_usd=wd_usd,
                epoch_reset=epoch_reset_val,
                data_source="api",
                written_at=now,
            )
        )

    db.upsert_high_freq_prices(rows, cutoff_timestamp=cutoff_timestamp)

    logger.debug(
        "Stored %d HF prices for vault %s (%s)",
        len(rows),
        info.name,
        vault_address,
    )
    return True


# ──────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────


def _process_vault_worker(
    session: HyperliquidSession,
    db: HyperliquidHighFreqMetricsDatabase,
    summary: VaultSummary,
    scan_interval: datetime.timedelta,
    cutoff_timestamp: datetime.datetime | None,
    timeout: float,
    flow_backfill_days: int,
) -> bool:
    """Worker function for parallel vault processing."""
    return fetch_and_store_vault_high_freq(
        session,
        db,
        summary,
        scan_interval=scan_interval,
        cutoff_timestamp=cutoff_timestamp,
        timeout=timeout,
        flow_backfill_days=flow_backfill_days,
    )


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────


def run_high_freq_scan(
    session: HyperliquidSession,
    db_path: Path = HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE,
    scan_interval: datetime.timedelta = HYPERLIQUID_HIGH_FREQ_DEFAULT_INTERVAL,
    min_tvl: float = 5_000,
    max_vaults: int = 20_000,
    max_workers: int = 16,
    cutoff_timestamp: datetime.datetime | None = None,
    timeout: float = 30.0,
    vault_addresses: list[str] | None = None,
    flow_backfill_days: int = 7,
    full_scan: bool = False,
) -> HyperliquidHighFreqMetricsDatabase:
    """Run a high-frequency Hyperliquid vault metrics scan.

    Mirrors :py:func:`~eth_defi.hyperliquid.daily_metrics.run_daily_scan`
    but stores rows keyed by ``(vault_address, timestamp)`` with
    configurable sub-daily granularity and proxy-aware parallelism.

    :param session:
        HTTP session (optionally with proxy rotator).
    :param db_path:
        Path to the HF DuckDB database file.
    :param scan_interval:
        Bucket size for timestamp normalisation.
    :param min_tvl:
        Minimum TVL in USD to include a vault.
    :param max_vaults:
        Maximum number of vaults to process.
    :param max_workers:
        Number of parallel workers.
    :param cutoff_timestamp:
        If provided, only store data up to this timestamp.
    :param timeout:
        HTTP request timeout.
    :param vault_addresses:
        If provided, only scan these specific vaults.
    :param flow_backfill_days:
        Days to backfill flow data.
    :param full_scan:
        Bypass min_tvl and process all tracked + API vaults.
    :return:
        The HF metrics database instance.
    """
    logger.info(
        "Starting HF Hyperliquid vault scan (interval=%s, flow_backfill_days=%d, full_scan=%s)",
        scan_interval,
        flow_backfill_days,
        full_scan,
    )

    db = HyperliquidHighFreqMetricsDatabase(db_path)

    # Fetch all vaults from stats-data API
    vault_summaries = None
    for attempt in range(3):
        try:
            vault_summaries = list(fetch_all_vaults(session, timeout=timeout))
            break
        except Exception as e:
            logger.warning(
                "Error fetching vault summaries (attempt %d/3): %s",
                attempt + 1,
                e,
            )
            continue

    if vault_summaries is None:
        raise RuntimeError("Failed to fetch vault summaries after 3 attempts")

    logger.info("Fetched %d total vaults from stats-data API", len(vault_summaries))

    # Filter vaults (same logic as daily pipeline)
    if vault_addresses is not None:
        address_set = {a.lower() for a in vault_addresses}
        filtered = [s for s in vault_summaries if s.vault_address.lower() in address_set]
    else:
        if full_scan:
            tracked = db.get_all_tracked_addresses()
            api_set = {s.vault_address.lower() for s in vault_summaries}
            tracked_in_api = tracked & api_set
            filtered = [s for s in vault_summaries if s.vault_address.lower() in tracked_in_api or float(s.tvl) >= min_tvl]
        else:
            filtered = [s for s in vault_summaries if float(s.tvl) >= min_tvl]

            # Add recently-tracked vaults for wind-down bars
            recently_tracked = db.get_recently_tracked_addresses(within_days=4)
            filtered_addrs = {s.vault_address.lower() for s in filtered}
            wind_down = [s for s in vault_summaries if s.vault_address.lower() in recently_tracked and s.vault_address.lower() not in filtered_addrs]
            filtered.extend(wind_down)
            if wind_down:
                logger.info(
                    "Added %d recently-tracked vaults below TVL threshold for wind-down bars",
                    len(wind_down),
                )

        filtered.sort(key=lambda s: float(s.tvl), reverse=True)
        filtered = filtered[:max_vaults]

    logger.info("Processing %d vaults", len(filtered))

    # Parallel fetch with proxy-aware session pool
    if filtered:
        n_workers = min(max_workers, len(filtered))

        # Pre-create bounded pool of cloned sessions
        session_pool = [session.clone_for_worker(proxy_start_index=i) for i in range(n_workers)]
        session_lock = threading.Lock()

        def _hf_worker(summary: VaultSummary) -> bool:
            with session_lock:
                worker_session = session_pool.pop()
            try:
                return _process_vault_worker(
                    worker_session,
                    db,
                    summary,
                    scan_interval=scan_interval,
                    cutoff_timestamp=cutoff_timestamp,
                    timeout=timeout,
                    flow_backfill_days=flow_backfill_days,
                )
            finally:
                with session_lock:
                    session_pool.append(worker_session)

        desc = "Fetching HF Hyperliquid vault details"
        results = Parallel(n_jobs=n_workers, backend="threading")(delayed(_hf_worker)(s) for s in tqdm(filtered, desc=desc))

        success_count = sum(1 for r in results if r)
        fail_count = sum(1 for r in results if not r)
    else:
        logger.info("No vaults matched filters, skipping parallel fetch")
        success_count = 0
        fail_count = 0

    # Lifecycle maintenance (runs unconditionally)
    if vault_addresses is None:
        processed_addresses = {s.vault_address.lower() for s in filtered}
        all_api_addresses = {s.vault_address.lower() for s in vault_summaries}

        stale_updates = []
        for summary in vault_summaries:
            addr = summary.vault_address.lower()
            if addr not in processed_addresses:
                stale_updates.append(
                    (
                        float(summary.tvl),
                        summary.is_closed,
                        float(summary.apr) if summary.apr is not None else None,
                        addr,
                    )
                )

        if stale_updates:
            db.update_vault_tvl_bulk(stale_updates)
            logger.info(
                "Updated TVL for %d unprocessed vaults from bulk API data",
                len(stale_updates),
            )

        db.mark_vaults_disappeared(all_api_addresses)

        tombstone_count = db.tombstone_stale_vaults(all_api_addresses, wind_down_days=4)
        if tombstone_count:
            logger.info(
                "Tombstoned %d vaults that fell out of the pipeline",
                tombstone_count,
            )

    db.save()

    logger.info(
        "HF scan complete. Processed %d vaults (%d successful, %d failed) into %s",
        len(filtered),
        success_count,
        fail_count,
        db_path,
    )

    return db
