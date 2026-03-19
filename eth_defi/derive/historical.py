"""DuckDB persistence for Derive funding rate and open interest history.

Stores hourly funding rate snapshots and daily open interest snapshots
for perpetual instruments. Incremental sync fetches the full available
history (back to instrument inception) and is crash-resumeable.

The sync is crash-resumeable: partial batches are safely re-inserted
on restart via ``INSERT OR IGNORE`` on natural primary keys.

Schema
------

Three tables:

- ``funding_rates`` -- hourly funding rate snapshots
- ``open_interest`` -- daily open interest snapshots
- ``sync_state`` -- per-instrument watermarks for incremental sync

Storage location
----------------

Default: ``~/.tradingstrategy/derive/funding-rates.duckdb``

Example::

    from pathlib import Path
    from eth_defi.derive.session import create_derive_session
    from eth_defi.derive.historical import DeriveFundingRateDatabase

    session = create_derive_session()
    db = DeriveFundingRateDatabase(Path("/tmp/funding-rates.duckdb"))

    inserted = db.sync_instrument(session, "ETH-PERP")
    print(f"Stored {inserted} new funding rate entries")

    df = db.get_funding_rates_dataframe("ETH-PERP")
    print(df.tail())

    oi_inserted = db.sync_open_interest_instrument(session, "ETH-PERP")
    print(f"Stored {oi_inserted} new open interest entries")

    oi_df = db.get_open_interest_dataframe("ETH-PERP")
    print(oi_df.tail())

    db.close()
"""

import datetime
import logging
import threading
from pathlib import Path

import duckdb
import pandas
from requests import Session
from tqdm_loggable.auto import tqdm
from tqdm_loggable.tqdm_logging import tqdm_logging

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.derive.api import (
    DERIVE_BLOCK_TIME_SECONDS,
    FundingRateEntry,
    OpenInterestEntry,
    fetch_funding_rate_history,
    fetch_open_interest_onchain,
    fetch_instrument_details,
)
from eth_defi.derive.constants import DERIVE_MAINNET_API_URL, DERIVE_MAINNET_RPC_URL

logger = logging.getLogger(__name__)


def _format_count(n: int) -> str:
    """Format a count with k/M suffixes for compact display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


#: Default DuckDB path for Derive funding rate history
DEFAULT_FUNDING_RATE_DB_PATH = Path("~/.tradingstrategy/derive/funding-rates.duckdb").expanduser()

#: Size of each API fetch chunk in days.
#:
#: The Derive API returns empty results for windows >= 30 days,
#: so we use 28-day chunks as the maximum safe window.
CHUNK_DAYS = 28

#: Maximum lookback for inception-date binary search (days).
#:
#: We probe up to this many days into the past to find the
#: first day an instrument had funding rate data.
MAX_INCEPTION_PROBE_DAYS = 1100

#: Data type name for sync state tracking
DATA_TYPE_FUNDING_RATES = "funding_rates"

#: Data type name for open interest sync state tracking
DATA_TYPE_OPEN_INTEREST = "open_interest"

#: Step size in days for on-chain OI historical backfill.
OI_STEP_DAYS = 1


def estimate_block_at_timestamp(
    w3: Web3,
    target_ts: int,
    latest_block: int | None = None,
    latest_ts: int | None = None,
) -> int:
    """Estimate the Derive Chain block number closest to a Unix timestamp.

    Uses linear interpolation from the current block.  Derive Chain has a
    stable 2-second block time so the estimate is accurate to within a
    handful of blocks (a few seconds), which is more than adequate for
    daily open interest snapshots.

    :param w3:
        Web3 instance connected to Derive Chain.
    :param target_ts:
        Target Unix timestamp (seconds, UTC).
    :param latest_block:
        Current block number. Fetched automatically if not provided.
    :param latest_ts:
        Timestamp of the current block. Fetched automatically if not provided.
    :return:
        Estimated block number (always >= 1).
    """
    if latest_block is None or latest_ts is None:
        blk = w3.eth.get_block("latest")
        latest_block = blk.number
        latest_ts = blk.timestamp

    seconds_diff = latest_ts - target_ts
    block_diff = int(seconds_diff / DERIVE_BLOCK_TIME_SECONDS)
    estimated = latest_block - block_diff
    return max(1, estimated)


class DeriveFundingRateDatabase:
    """DuckDB database for storing Derive funding rate history.

    Stores hourly funding rate snapshots for perpetual instruments
    at the native resolution provided by Derive (one entry per hour).

    On first sync, fetches the full available history back to
    ``DEFAULT_LOOKBACK_DAYS`` using 1-day API chunks.  On subsequent
    syncs, fetches only new data since the last stored timestamp.

    The database is crash-resumeable: interrupted syncs can be safely
    re-run without data loss or duplicates.

    Thread safety: all database operations are protected by an internal
    lock. Multiple threads can call sync methods concurrently — the
    API calls run in parallel while database writes are serialised.
    """

    def __init__(self, path: Path = DEFAULT_FUNDING_RATE_DB_PATH):
        """Open or create a DuckDB database for funding rate storage.

        :param path:
            Path to the DuckDB file. Parent directories are created
            automatically if they do not exist.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = duckdb.connect(str(path))
        self._lock = threading.Lock()
        self._init_schema()

    def __del__(self):
        self.close()

    def close(self):
        """Close the database connection."""
        if hasattr(self, "conn") and self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def save(self):
        """Force a checkpoint to ensure data is persisted to disk."""
        with self._lock:
            self.conn.execute("CHECKPOINT")

    def _init_schema(self):
        """Create tables if they do not exist."""
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS funding_rates (
                    instrument VARCHAR NOT NULL,
                    ts BIGINT NOT NULL,
                    funding_rate DOUBLE NOT NULL,
                    PRIMARY KEY (instrument, ts)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS open_interest (
                    instrument VARCHAR NOT NULL,
                    ts BIGINT NOT NULL,
                    open_interest DOUBLE NOT NULL,
                    PRIMARY KEY (instrument, ts)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    instrument VARCHAR NOT NULL,
                    data_type VARCHAR NOT NULL,
                    oldest_ts BIGINT,
                    newest_ts BIGINT,
                    row_count INTEGER,
                    last_synced BIGINT NOT NULL,
                    PRIMARY KEY (instrument, data_type)
                )
            """)

    def find_inception_date(
        self,
        session: Session,
        instrument_name: str,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
    ) -> datetime.datetime | None:
        """Find the earliest available funding rate data for an instrument.

        Uses binary search over day-sized probes to locate the first
        day with data.  Typically requires ~10 API calls.

        :param session:
            HTTP session.
        :param instrument_name:
            Perpetual instrument name.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout.
        :return:
            Start of the earliest day with data (naive UTC),
            or ``None`` if no data exists at all.
        """
        now = native_datetime_utc_now()
        one_day = datetime.timedelta(days=1)

        # Quick check: does any recent data exist?
        recent = fetch_funding_rate_history(
            session,
            instrument_name,
            start_time=now - datetime.timedelta(days=7),
            end_time=now,
            base_url=base_url,
            timeout=timeout,
        )
        if not recent:
            return None

        # lo = days ago where data EXISTS (closer to now)
        # hi = days ago where data does NOT exist (further back)
        lo = 7
        hi = MAX_INCEPTION_PROBE_DAYS

        # Check if data exists at the maximum lookback
        probe_start = now - datetime.timedelta(days=hi)
        probe = fetch_funding_rate_history(
            session,
            instrument_name,
            start_time=probe_start,
            end_time=probe_start + one_day,
            base_url=base_url,
            timeout=timeout,
        )
        if probe:
            # Data goes back further than our max probe window
            logger.info(
                "Instrument %s has data at %d days ago, using that as inception",
                instrument_name,
                hi,
            )
            return probe_start

        # Binary search for the boundary
        while hi - lo > 1:
            mid = (lo + hi) // 2
            probe_start = now - datetime.timedelta(days=mid)
            probe = fetch_funding_rate_history(
                session,
                instrument_name,
                start_time=probe_start,
                end_time=probe_start + one_day,
                base_url=base_url,
                timeout=timeout,
            )
            if probe:
                lo = mid
            else:
                hi = mid

        inception = now - datetime.timedelta(days=lo)
        inception = inception.replace(hour=0, minute=0, second=0, microsecond=0)
        logger.info(
            "Found inception date for %s: %s (%d days ago)",
            instrument_name,
            inception.strftime("%Y-%m-%d"),
            lo,
        )
        return inception

    def sync_instrument(
        self,
        session: Session,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
        progress: tqdm | None = None,
    ) -> int:
        """Fetch funding rate data and store it.

        Walks the time range in :py:data:`CHUNK_DAYS`-sized windows
        (1 day by default) to fetch the full available history from
        the Derive API.

        On the first run, uses :py:meth:`find_inception_date` to
        discover how far back data is available, then fetches from
        inception to now.  On subsequent runs, resumes forward from
        the last stored timestamp.

        :param session:
            HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
        :param instrument_name:
            Perpetual instrument name (e.g. ``"ETH-PERP"``).
        :param start_time:
            Override start time (naive UTC). Defaults to resume point
            or instrument inception date.
        :param end_time:
            Override end time (naive UTC). Defaults to now.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout.
        :param progress:
            Optional external :py:class:`tqdm` progress bar to update
            per chunk.  When ``None``, no progress bar is shown (use
            :py:meth:`sync_instruments` for progress tracking).
        :return:
            Number of new funding rate entries inserted.
        """
        now = native_datetime_utc_now()

        # Determine effective start time
        if start_time is None:
            state = self._get_sync_state_row(instrument_name)
            if state is not None and state["newest_ts"] is not None:
                # Resume from last known timestamp
                start_time = datetime.datetime.fromtimestamp(
                    state["newest_ts"] / 1000,
                    tz=datetime.timezone.utc,
                ).replace(tzinfo=None)
            else:
                # First run: discover inception date
                inception = self.find_inception_date(
                    session,
                    instrument_name,
                    base_url=base_url,
                    timeout=timeout,
                )
                if inception is None:
                    logger.info("No funding rate data available for %s", instrument_name)
                    return 0
                start_time = inception

        if end_time is None:
            end_time = now

        # Walk the window in day-sized chunks
        total_inserted = 0
        chunk_delta = datetime.timedelta(days=CHUNK_DAYS)
        chunk_start = start_time

        while chunk_start < end_time:
            chunk_end = min(chunk_start + chunk_delta, end_time)

            entries = fetch_funding_rate_history(
                session,
                instrument_name,
                start_time=chunk_start,
                end_time=chunk_end,
                base_url=base_url,
                timeout=timeout,
            )

            if entries:
                total_inserted += self._insert_batch(entries)

            chunk_start = chunk_end
            if progress is not None:
                progress.update(1)

        if total_inserted > 0:
            self._update_sync_state(instrument_name)

        logger.info(
            "Inserted %d new funding rate entries for %s",
            total_inserted,
            instrument_name,
        )
        return total_inserted

    def sync_instruments(
        self,
        session: Session,
        instrument_names: list[str],
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
    ) -> dict[str, int]:
        """Sync funding rate history for multiple instruments.

        Two-phase process:

        1. **Probe** — determine the effective time range for each
           instrument (inception date or resume point).
        2. **Fetch** — walk all instruments day-by-day with a single
           progress bar showing total days remaining.

        :param session:
            HTTP session.
        :param instrument_names:
            List of instrument names (e.g. ``["ETH-PERP", "BTC-PERP"]``).
        :param start_time:
            Override start time for all instruments.
        :param end_time:
            Override end time for all instruments.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout.
        :return:
            Dict mapping instrument name to number of new entries inserted.
        """
        now = native_datetime_utc_now()
        if end_time is None:
            end_time = now

        tqdm_logging.set_level(logging.INFO)

        # Phase 1: determine effective start for each instrument
        ranges: dict[str, datetime.datetime] = {}
        probe_bar = tqdm(instrument_names, desc="Probing inception dates", unit="instrument")
        for name in probe_bar:
            probe_bar.set_postfix(instrument=name)

            if start_time is not None:
                ranges[name] = start_time
                continue

            state = self._get_sync_state_row(name)
            if state is not None and state["newest_ts"] is not None:
                ranges[name] = datetime.datetime.fromtimestamp(
                    state["newest_ts"] / 1000,
                    tz=datetime.timezone.utc,
                ).replace(tzinfo=None)
            else:
                inception = self.find_inception_date(
                    session,
                    name,
                    base_url=base_url,
                    timeout=timeout,
                )
                if inception is not None:
                    ranges[name] = inception
                else:
                    logger.warning("No data available for %s, skipping", name)
        probe_bar.close()

        if not ranges:
            return {}

        # Phase 2: fetch with a chunk-level progress bar
        total_chunks = sum(max((end_time - inst_start).days // CHUNK_DAYS + 1, 1) for inst_start in ranges.values())

        results = {}
        total_new = 0
        fetch_bar = tqdm(total=total_chunks, desc="Fetching funding rates", unit="chunk")
        for name, inst_start in ranges.items():
            fetch_bar.set_postfix(
                instrument=name,
                range=f"{inst_start.strftime('%Y-%m-%d')}..{end_time.strftime('%Y-%m-%d')}",
                rows=_format_count(total_new),
            )
            inserted = self.sync_instrument(
                session,
                name,
                start_time=inst_start,
                end_time=end_time,
                base_url=base_url,
                timeout=timeout,
                progress=fetch_bar,
            )
            results[name] = inserted
            total_new += inserted
        fetch_bar.set_postfix(rows=_format_count(total_new))
        fetch_bar.close()

        return results

    def get_funding_rates(
        self,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[FundingRateEntry]:
        """Get stored funding rate entries for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :param start_time:
            Optional start time filter (naive UTC).
        :param end_time:
            Optional end time filter (naive UTC).
        :return:
            List of FundingRateEntry objects sorted by timestamp ascending.
        """
        query = "SELECT instrument, ts, funding_rate FROM funding_rates WHERE instrument = ?"
        params: list = [instrument_name]

        if start_time is not None:
            ts_ms = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts >= ?"
            params.append(ts_ms)

        if end_time is not None:
            ts_ms = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts <= ?"
            params.append(ts_ms)

        query += " ORDER BY ts ASC"

        with self._lock:
            rows = self.conn.execute(query, params).fetchall()

        from decimal import Decimal

        return [
            FundingRateEntry(
                instrument=row[0],
                timestamp=datetime.datetime.fromtimestamp(row[1] / 1000, tz=datetime.timezone.utc).replace(tzinfo=None),
                timestamp_ms=row[1],
                funding_rate=Decimal(str(row[2])),
            )
            for row in rows
        ]

    def get_funding_rates_dataframe(
        self,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> pandas.DataFrame:
        """Get stored funding rates as a Pandas DataFrame.

        Columns: ``timestamp``, ``instrument``, ``funding_rate``.

        :param instrument_name:
            Perpetual instrument name.
        :param start_time:
            Optional start time filter (naive UTC).
        :param end_time:
            Optional end time filter (naive UTC).
        :return:
            DataFrame with funding rate data.
        """
        query = "SELECT instrument, ts, funding_rate FROM funding_rates WHERE instrument = ?"
        params: list = [instrument_name]

        if start_time is not None:
            ts_ms = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts >= ?"
            params.append(ts_ms)

        if end_time is not None:
            ts_ms = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts <= ?"
            params.append(ts_ms)

        query += " ORDER BY ts ASC"

        with self._lock:
            df = self.conn.execute(query, params).df()

        # Convert ms timestamps to datetime column
        if len(df) > 0:
            df["timestamp"] = pandas.to_datetime(df["ts"], unit="ms", utc=False)
            df = df.drop(columns=["ts"])
        else:
            df = pandas.DataFrame(columns=["instrument", "funding_rate", "timestamp"])

        return df

    def get_row_count(self, instrument_name: str) -> int:
        """Get the number of stored entries for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :return:
            Number of stored funding rate entries.
        """
        with self._lock:
            result = self.conn.execute(
                "SELECT COUNT(*) FROM funding_rates WHERE instrument = ?",
                [instrument_name],
            ).fetchone()
        return result[0] if result else 0

    def get_sync_state(self, instrument_name: str, data_type: str = DATA_TYPE_FUNDING_RATES) -> dict | None:
        """Get sync state for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :param data_type:
            Sync state data type key. Defaults to ``DATA_TYPE_FUNDING_RATES``.
        :return:
            Dict with ``oldest_ts``, ``newest_ts``, ``row_count``,
            ``last_synced``, or ``None`` if no sync has occurred.
        """
        return self._get_sync_state_row(instrument_name, data_type)

    def sync_open_interest_instrument(
        self,
        session: Session,
        instrument_name: str,
        w3: Web3 | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
        progress: tqdm | None = None,
    ) -> int:
        """Fetch open interest history and store it in DuckDB.

        Queries the ``openInterest(uint256)`` view function on the Derive
        Chain perp contract at daily intervals.  Derive Chain is an archive
        node so historical state from chain genesis is available.

        On the first run, fetches daily snapshots from the instrument's
        ``scheduled_activation`` date to now.  On subsequent runs, resumes
        from the last stored timestamp.  Uses ``INSERT OR IGNORE`` so the
        sync is crash-resumeable.

        :param session:
            HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
            Used to look up the instrument's on-chain contract address.
        :param instrument_name:
            Perpetual instrument name (e.g. ``"ETH-PERP"``).
        :param w3:
            Web3 instance connected to Derive Chain
            (``https://rpc.derive.xyz``).  Created automatically if
            not provided.
        :param start_time:
            Override start time (naive UTC). Defaults to last synced
            timestamp or instrument activation date.
        :param end_time:
            Override end time (naive UTC). Defaults to now.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout for instrument detail lookup.
        :param progress:
            Optional external :py:class:`tqdm` progress bar to update
            per day.
        :return:
            Number of new open interest entries inserted.
        """
        if w3 is None:
            w3 = Web3(Web3.HTTPProvider(DERIVE_MAINNET_RPC_URL))

        now = native_datetime_utc_now()
        if end_time is None:
            end_time = now

        # Look up contract address for this instrument
        details = fetch_instrument_details(session, base_url=base_url, timeout=timeout)
        inst_info = details.get(instrument_name)
        if inst_info is None:
            logger.warning("Instrument %s not found in active instruments list", instrument_name)
            return 0

        contract_address = inst_info["base_asset_address"]
        activation_ts = inst_info["scheduled_activation"]  # Unix seconds
        activation_dt = datetime.datetime.fromtimestamp(activation_ts, tz=datetime.timezone.utc).replace(tzinfo=None)

        # Determine effective start
        if start_time is None:
            state = self._get_sync_state_row(instrument_name, DATA_TYPE_OPEN_INTEREST)
            if state is not None and state["newest_ts"] is not None:
                start_time = datetime.datetime.fromtimestamp(
                    state["newest_ts"] / 1000,
                    tz=datetime.timezone.utc,
                ).replace(tzinfo=None)
                # Advance by one day so we don't re-insert the last known point
                start_time += datetime.timedelta(days=OI_STEP_DAYS)
            else:
                # First run: start from activation date (aligned to midnight)
                start_time = activation_dt.replace(hour=0, minute=0, second=0, microsecond=0)

        # Cache latest block so we only fetch it once per call
        latest_blk = w3.eth.get_block("latest")
        latest_block = latest_blk.number
        latest_ts_unix = latest_blk.timestamp

        total_inserted = 0
        current = start_time
        step = datetime.timedelta(days=OI_STEP_DAYS)

        while current <= end_time:
            target_ts_unix = int(current.replace(tzinfo=datetime.timezone.utc).timestamp())
            block = estimate_block_at_timestamp(
                w3,
                target_ts_unix,
                latest_block=latest_block,
                latest_ts=latest_ts_unix,
            )

            oi = fetch_open_interest_onchain(w3, contract_address, block)

            if oi is not None:
                ts_ms = int(current.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
                entry = OpenInterestEntry(
                    instrument=instrument_name,
                    timestamp=current,
                    timestamp_ms=ts_ms,
                    open_interest=oi,
                )
                total_inserted += self._insert_open_interest_batch([entry])

            current += step
            if progress is not None:
                progress.update(1)

        if total_inserted > 0:
            self._update_sync_state(instrument_name, DATA_TYPE_OPEN_INTEREST, "open_interest")

        logger.info(
            "Inserted %d new open interest entries for %s",
            total_inserted,
            instrument_name,
        )
        return total_inserted

    def sync_open_interest_instruments(
        self,
        session: Session,
        instrument_names: list[str],
        w3: Web3 | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
    ) -> dict[str, int]:
        """Fetch open interest history for multiple instruments.

        Walks each instrument day-by-day from inception (or last stored
        timestamp) to now, reading on-chain state at daily intervals.

        :param session:
            HTTP session.
        :param instrument_names:
            List of instrument names (e.g. ``["ETH-PERP", "BTC-PERP"]``).
        :param w3:
            Web3 instance connected to Derive Chain.  Created automatically
            if not provided.
        :param start_time:
            Override start time for all instruments.
        :param end_time:
            Override end time for all instruments.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout.
        :return:
            Dict mapping instrument name to number of new entries inserted.
        """
        if w3 is None:
            w3 = Web3(Web3.HTTPProvider(DERIVE_MAINNET_RPC_URL))

        tqdm_logging.set_level(logging.INFO)

        now = native_datetime_utc_now()
        if end_time is None:
            end_time = now

        # Fetch instrument details once for all instruments
        details = fetch_instrument_details(session, base_url=base_url, timeout=timeout)

        # Cache latest block for all instruments
        latest_blk = w3.eth.get_block("latest")
        latest_block = latest_blk.number
        latest_ts_unix = latest_blk.timestamp

        # Determine effective start for each instrument and total day count
        ranges: dict[str, datetime.datetime] = {}
        for name in instrument_names:
            inst_info = details.get(name)
            if inst_info is None:
                logger.warning("Instrument %s not found, skipping", name)
                continue

            if start_time is not None:
                ranges[name] = start_time
                continue

            state = self._get_sync_state_row(name, DATA_TYPE_OPEN_INTEREST)
            if state is not None and state["newest_ts"] is not None:
                resume = datetime.datetime.fromtimestamp(
                    state["newest_ts"] / 1000,
                    tz=datetime.timezone.utc,
                ).replace(tzinfo=None)
                ranges[name] = resume + datetime.timedelta(days=OI_STEP_DAYS)
            else:
                activation_ts = inst_info["scheduled_activation"]
                activation_dt = datetime.datetime.fromtimestamp(activation_ts, tz=datetime.timezone.utc).replace(tzinfo=None)
                ranges[name] = activation_dt.replace(hour=0, minute=0, second=0, microsecond=0)

        if not ranges:
            return {}

        total_days = sum(max(int((end_time - inst_start).days), 0) + 1 for inst_start in ranges.values())

        results: dict[str, int] = {}
        total_new = 0
        bar = tqdm(total=total_days, desc="Fetching OI on-chain", unit="day")

        for name, inst_start in ranges.items():
            inst_info = details[name]
            contract_address = inst_info["base_asset_address"]
            bar.set_postfix(instrument=name, rows=_format_count(total_new))

            current = inst_start
            step = datetime.timedelta(days=OI_STEP_DAYS)
            inserted = 0

            while current <= end_time:
                target_ts_unix = int(current.replace(tzinfo=datetime.timezone.utc).timestamp())
                block = estimate_block_at_timestamp(
                    w3,
                    target_ts_unix,
                    latest_block=latest_block,
                    latest_ts=latest_ts_unix,
                )

                oi = fetch_open_interest_onchain(w3, contract_address, block)

                if oi is not None:
                    ts_ms = int(current.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
                    entry = OpenInterestEntry(
                        instrument=name,
                        timestamp=current,
                        timestamp_ms=ts_ms,
                        open_interest=oi,
                    )
                    inserted += self._insert_open_interest_batch([entry])

                current += step
                bar.update(1)

            if inserted > 0:
                self._update_sync_state(name, DATA_TYPE_OPEN_INTEREST, "open_interest")

            results[name] = inserted
            total_new += inserted

        bar.set_postfix(rows=_format_count(total_new))
        bar.close()

        return results

    def get_open_interest(
        self,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[OpenInterestEntry]:
        """Get stored open interest entries for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :param start_time:
            Optional start time filter (naive UTC).
        :param end_time:
            Optional end time filter (naive UTC).
        :return:
            List of :py:class:`~eth_defi.derive.api.OpenInterestEntry`
            objects sorted by timestamp ascending.
        """
        from decimal import Decimal

        query = "SELECT instrument, ts, open_interest FROM open_interest WHERE instrument = ?"
        params: list = [instrument_name]

        if start_time is not None:
            ts_ms = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts >= ?"
            params.append(ts_ms)

        if end_time is not None:
            ts_ms = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts <= ?"
            params.append(ts_ms)

        query += " ORDER BY ts ASC"

        with self._lock:
            rows = self.conn.execute(query, params).fetchall()

        return [
            OpenInterestEntry(
                instrument=row[0],
                timestamp=datetime.datetime.fromtimestamp(row[1] / 1000, tz=datetime.timezone.utc).replace(tzinfo=None),
                timestamp_ms=row[1],
                open_interest=Decimal(str(row[2])),
            )
            for row in rows
        ]

    def get_open_interest_dataframe(
        self,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> pandas.DataFrame:
        """Get stored open interest as a Pandas DataFrame.

        Columns: ``timestamp``, ``instrument``, ``open_interest``.

        :param instrument_name:
            Perpetual instrument name.
        :param start_time:
            Optional start time filter (naive UTC).
        :param end_time:
            Optional end time filter (naive UTC).
        :return:
            DataFrame with open interest data.
        """
        query = "SELECT instrument, ts, open_interest FROM open_interest WHERE instrument = ?"
        params: list = [instrument_name]

        if start_time is not None:
            ts_ms = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts >= ?"
            params.append(ts_ms)

        if end_time is not None:
            ts_ms = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            query += " AND ts <= ?"
            params.append(ts_ms)

        query += " ORDER BY ts ASC"

        with self._lock:
            df = self.conn.execute(query, params).df()

        if len(df) > 0:
            df["timestamp"] = pandas.to_datetime(df["ts"], unit="ms", utc=False)
            df = df.drop(columns=["ts"])
        else:
            df = pandas.DataFrame(columns=["instrument", "open_interest", "timestamp"])

        return df

    def get_open_interest_row_count(self, instrument_name: str) -> int:
        """Get the number of stored open interest entries for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :return:
            Number of stored open interest entries.
        """
        with self._lock:
            result = self.conn.execute(
                "SELECT COUNT(*) FROM open_interest WHERE instrument = ?",
                [instrument_name],
            ).fetchone()
        return result[0] if result else 0

    def get_open_interest_sync_state(self, instrument_name: str) -> dict | None:
        """Get open interest sync state for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :return:
            Dict with ``oldest_ts``, ``newest_ts``, ``row_count``,
            ``last_synced``, or ``None`` if no sync has occurred.
        """
        return self._get_sync_state_row(instrument_name, DATA_TYPE_OPEN_INTEREST)

    def _insert_open_interest_batch(self, entries: list[OpenInterestEntry]) -> int:
        """Insert a batch of open interest rows, ignoring duplicates.

        :param entries:
            List of :py:class:`~eth_defi.derive.api.OpenInterestEntry` objects to insert.
        :return:
            Number of new rows actually inserted.
        """
        if not entries:
            return 0

        rows = [(e.instrument, e.timestamp_ms, float(e.open_interest)) for e in entries]

        with self._lock:
            count_before = self.conn.execute(
                "SELECT COUNT(*) FROM open_interest WHERE instrument = ?",
                [entries[0].instrument],
            ).fetchone()[0]

            self.conn.executemany(
                "INSERT OR IGNORE INTO open_interest (instrument, ts, open_interest) VALUES (?, ?, ?)",
                rows,
            )

            count_after = self.conn.execute(
                "SELECT COUNT(*) FROM open_interest WHERE instrument = ?",
                [entries[0].instrument],
            ).fetchone()[0]

        return count_after - count_before

    def _insert_batch(self, entries: list[FundingRateEntry]) -> int:
        """Insert a batch of funding rate rows, ignoring duplicates.

        :param entries:
            List of FundingRateEntry objects to insert.
        :return:
            Number of new rows actually inserted.
        """
        if not entries:
            return 0

        rows = [(e.instrument, e.timestamp_ms, float(e.funding_rate)) for e in entries]

        with self._lock:
            count_before = self.conn.execute(
                "SELECT COUNT(*) FROM funding_rates WHERE instrument = ?",
                [entries[0].instrument],
            ).fetchone()[0]

            self.conn.executemany(
                "INSERT OR IGNORE INTO funding_rates (instrument, ts, funding_rate) VALUES (?, ?, ?)",
                rows,
            )

            count_after = self.conn.execute(
                "SELECT COUNT(*) FROM funding_rates WHERE instrument = ?",
                [entries[0].instrument],
            ).fetchone()[0]

        return count_after - count_before

    def _update_sync_state(self, instrument_name: str, data_type: str = DATA_TYPE_FUNDING_RATES, table_name: str = "funding_rates"):
        """Recompute and store sync state for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :param data_type:
            Sync state data type key (e.g. ``DATA_TYPE_FUNDING_RATES``).
        :param table_name:
            DuckDB table to aggregate stats from.
        """
        now_ms = int(native_datetime_utc_now().replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

        with self._lock:
            stats = self.conn.execute(
                f"SELECT MIN(ts), MAX(ts), COUNT(*) FROM {table_name} WHERE instrument = ?",
                [instrument_name],
            ).fetchone()

            oldest_ts, newest_ts, row_count = stats

            self.conn.execute(
                """
                INSERT INTO sync_state (instrument, data_type, oldest_ts, newest_ts, row_count, last_synced)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (instrument, data_type) DO UPDATE SET
                    oldest_ts = excluded.oldest_ts,
                    newest_ts = excluded.newest_ts,
                    row_count = excluded.row_count,
                    last_synced = excluded.last_synced
                """,
                [instrument_name, data_type, oldest_ts, newest_ts, row_count, now_ms],
            )

    def _get_sync_state_row(self, instrument_name: str, data_type: str = DATA_TYPE_FUNDING_RATES) -> dict | None:
        """Get sync state row for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :param data_type:
            Sync state data type key (e.g. ``DATA_TYPE_FUNDING_RATES``).
        :return:
            Dict with state fields, or ``None`` if no sync has occurred.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT oldest_ts, newest_ts, row_count, last_synced FROM sync_state WHERE instrument = ? AND data_type = ?",
                [instrument_name, data_type],
            ).fetchone()

        if row is None:
            return None

        return {
            "oldest_ts": row[0],
            "newest_ts": row[1],
            "row_count": row[2],
            "last_synced": row[3],
        }
