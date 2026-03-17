"""DuckDB persistence for Derive funding rate history.

Stores hourly funding rate snapshots for perpetual instruments.
Incremental sync accumulates data beyond the 30-day API window
by fetching successive chunks and storing them in DuckDB.

The sync is crash-resumeable: partial batches are safely re-inserted
on restart via ``INSERT OR IGNORE`` on natural primary keys.

Schema
------

Two tables:

- ``funding_rates`` -- hourly funding rate snapshots
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

from eth_defi.compat import native_datetime_utc_now
from eth_defi.derive.api import FundingRateEntry, fetch_funding_rate_history
from eth_defi.derive.constants import DERIVE_MAINNET_API_URL

logger = logging.getLogger(__name__)

#: Default DuckDB path for Derive funding rate history
DEFAULT_FUNDING_RATE_DB_PATH = Path("~/.tradingstrategy/derive/funding-rates.duckdb").expanduser()

#: Maximum API window in days (API constraint)
MAX_API_WINDOW_DAYS = 30

#: Data type name for sync state tracking
DATA_TYPE_FUNDING_RATES = "funding_rates"


class DeriveFundingRateDatabase:
    """DuckDB database for storing Derive funding rate history.

    Stores hourly funding rate snapshots for perpetual instruments
    at the native resolution provided by Derive (one entry per hour).

    Supports incremental sync that accumulates data beyond the
    30-day API window limit by running regularly.

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

    def sync_instrument(
        self,
        session: Session,
        instrument_name: str,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        base_url: str = DERIVE_MAINNET_API_URL,
        timeout: float = 30.0,
    ) -> int:
        """Fetch new funding rate data and store it.

        On the first run, fetches the maximum available history
        (30 days). On subsequent runs, resumes from the last
        stored timestamp.

        The API limits ``start_time`` to at most 30 days ago.
        If the last sync is older than 30 days, data in the gap
        between the last sync and 30 days ago is lost — run this
        at least once every 30 days to avoid gaps.

        :param session:
            HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
        :param instrument_name:
            Perpetual instrument name (e.g. ``"ETH-PERP"``).
        :param start_time:
            Override start time (naive UTC). Defaults to resume point
            or 30 days ago.
        :param end_time:
            Override end time (naive UTC). Defaults to now.
        :param base_url:
            Derive API base URL.
        :param timeout:
            HTTP request timeout.
        :return:
            Number of new funding rate entries inserted.
        """
        now = native_datetime_utc_now()
        max_lookback = now - datetime.timedelta(days=MAX_API_WINDOW_DAYS)

        # Determine effective start time
        if start_time is None:
            state = self._get_sync_state_row(instrument_name)
            if state is not None and state["newest_ts"] is not None:
                # Resume from last known timestamp
                resume_ts = datetime.datetime.fromtimestamp(state["newest_ts"] / 1000, tz=datetime.timezone.utc).replace(tzinfo=None)
                start_time = max(resume_ts, max_lookback)
            else:
                start_time = max_lookback

        if end_time is None:
            end_time = now

        # Clamp start_time to API limit
        if start_time < max_lookback:
            logger.warning(
                "Clamping start_time from %s to %s (API 30-day limit)",
                start_time,
                max_lookback,
            )
            start_time = max_lookback

        # Fetch from API
        entries = fetch_funding_rate_history(
            session,
            instrument_name,
            start_time=start_time,
            end_time=end_time,
            base_url=base_url,
            timeout=timeout,
        )

        if not entries:
            logger.info("No new funding rate data for %s", instrument_name)
            return 0

        # Insert into DuckDB
        inserted = self._insert_batch(entries)
        self._update_sync_state(instrument_name)

        logger.info(
            "Inserted %d new funding rate entries for %s (total fetched: %d)",
            inserted,
            instrument_name,
            len(entries),
        )
        return inserted

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

        Iterates over instruments with a progress bar.

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
        results = {}
        tqdm_logging.set_level(logging.DEBUG)
        progress = tqdm(instrument_names, desc="Syncing funding rates", unit="instrument")
        for name in progress:
            progress.set_postfix(instrument=name)
            inserted = self.sync_instrument(
                session,
                name,
                start_time=start_time,
                end_time=end_time,
                base_url=base_url,
                timeout=timeout,
            )
            results[name] = inserted
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

    def get_sync_state(self, instrument_name: str) -> dict | None:
        """Get sync state for an instrument.

        :param instrument_name:
            Perpetual instrument name.
        :return:
            Dict with ``oldest_ts``, ``newest_ts``, ``row_count``,
            ``last_synced``, or ``None`` if no sync has occurred.
        """
        return self._get_sync_state_row(instrument_name)

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

    def _update_sync_state(self, instrument_name: str):
        """Recompute and store sync state for an instrument."""
        now_ms = int(native_datetime_utc_now().replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

        with self._lock:
            stats = self.conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM funding_rates WHERE instrument = ?",
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
                [instrument_name, DATA_TYPE_FUNDING_RATES, oldest_ts, newest_ts, row_count, now_ms],
            )

    def _get_sync_state_row(self, instrument_name: str) -> dict | None:
        """Get sync state row for an instrument.

        :return:
            Dict with state fields, or ``None`` if no sync has occurred.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT oldest_ts, newest_ts, row_count, last_synced FROM sync_state WHERE instrument = ? AND data_type = ?",
                [instrument_name, DATA_TYPE_FUNDING_RATES],
            ).fetchone()

        if row is None:
            return None

        return {
            "oldest_ts": row[0],
            "newest_ts": row[1],
            "row_count": row[2],
            "last_synced": row[3],
        }
