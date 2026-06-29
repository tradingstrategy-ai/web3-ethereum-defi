"""DuckDB persistence for historical exchange rates.

Stores one row per ``(date, base_currency, quote_currency, source)`` plus a
quote-level gap table (``unavailable_rates``) so the scanner can resume on
completeness rather than on ``MAX(date)`` and never re-fetch genuinely missing
cells forever.

See ``scripts/currency_api/README-currency-api.md`` for the schema overview.
"""

import datetime
import logging
from pathlib import Path

import duckdb
import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.client import DateRates

logger = logging.getLogger(__name__)


class CurrencyRateDatabase:
    """DuckDB database for storing historical exchange rates.

    Three tables:

    - ``exchange_rates`` — the data, keyed by
      ``(date, base_currency, quote_currency, source)``. ``rate`` is the raw API
      value (units of quote per 1 unit of base).
    - ``unavailable_rates`` — quote-level gap tracking for cells confirmed to
      have no data (whole-date 404s, individually missing quotes and given-up
      persistent errors), so they are not re-fetched on every run.
    - ``fetch_attempts`` — internal bookkeeping of the consecutive
      transient-failure count per ``(date, base_currency, source)``, used to give
      up on a stuck date after a bounded number of attempts.

    Example::

        from pathlib import Path
        from eth_defi.currency_api.database import CurrencyRateDatabase

        db = CurrencyRateDatabase(Path("/tmp/rates.duckdb"))
        print(db.get_rates_dataframe())
        db.close()
    """

    def __init__(self, path: Path) -> None:
        """Open (or create) the database and ensure the schema exists.

        :param path:
            Path to the DuckDB file. Parent directories are created if needed.
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

    def _init_schema(self) -> None:
        """Create tables if they do not exist."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS exchange_rates (
                date DATE NOT NULL,
                base_currency VARCHAR NOT NULL,
                quote_currency VARCHAR NOT NULL,
                rate DOUBLE NOT NULL,
                source VARCHAR NOT NULL,
                written_at TIMESTAMP,
                PRIMARY KEY (date, base_currency, quote_currency, source)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS unavailable_rates (
                date DATE NOT NULL,
                base_currency VARCHAR NOT NULL,
                quote_currency VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                reason VARCHAR,
                http_status INTEGER,
                checked_at TIMESTAMP,
                PRIMARY KEY (date, base_currency, quote_currency, source)
            )
        """)

        # Consecutive transient-failure counter per date, so a date that keeps
        # failing with a non-404 error (403/5xx/network) across runs can be given
        # up on after a bounded number of attempts instead of retrying forever.
        # Reset (deleted) as soon as a date succeeds or is confirmed unavailable.
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS fetch_attempts (
                date DATE NOT NULL,
                base_currency VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                transient_attempts INTEGER NOT NULL,
                last_attempt_at TIMESTAMP,
                PRIMARY KEY (date, base_currency, source)
            )
        """)

    def save(self) -> None:
        """Force a checkpoint so data is flushed to disk."""
        self.con.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self.con is not None:
            logger.info("Closing currency rate database at %s", self.path)
            self.con.close()
            self.con = None

    def upsert_rates(self, date_rates: DateRates) -> None:
        """Idempotently upsert the rates for one date.

        Re-running with the same key overwrites the value and ``written_at``
        (``ON CONFLICT DO UPDATE``), so a repeated scan produces no duplicates.

        :param date_rates:
            Parsed rates to store.
        """
        if not date_rates.rows:
            return

        written_at = native_datetime_utc_now()
        params = [
            (
                date_rates.date,
                date_rates.base_currency,
                quote,
                rate,
                date_rates.source,
                written_at,
            )
            for quote, rate in date_rates.rows
        ]

        self.con.executemany(
            """
            INSERT INTO exchange_rates (
                date, base_currency, quote_currency, rate, source, written_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (date, base_currency, quote_currency, source)
            DO UPDATE SET
                rate = EXCLUDED.rate,
                written_at = EXCLUDED.written_at
            """,
            params,
        )

        # A cell that now has data is no longer a gap: keep exchange_rates and
        # unavailable_rates mutually exclusive so a previously-recorded gap
        # (quote_missing / date_404 / persistent_error) cannot linger as a stale row.
        self.con.executemany(
            """
            DELETE FROM unavailable_rates
            WHERE date = ? AND base_currency = ? AND quote_currency = ? AND source = ?
            """,
            [(date_rates.date, date_rates.base_currency, quote, date_rates.source) for quote, _ in date_rates.rows],
        )

    def record_unavailable(
        self,
        date: datetime.date,
        base_currency: str,
        quote_currency: str,
        source: str,
        reason: str,
        http_status: int | None = None,
    ) -> None:
        """Record a single rate cell as permanently unavailable.

        :param date:
            Date of the missing cell.
        :param base_currency:
            Base currency code.
        :param quote_currency:
            Quote currency code that has no data.
        :param source:
            Provider identifier.
        :param reason:
            ``date_404`` (whole date 404 on both hosts) or ``quote_missing``
            (quote absent from an otherwise-200 body).
        :param http_status:
            HTTP status that confirmed the gap, if applicable.
        """
        self.con.execute(
            """
            INSERT INTO unavailable_rates (
                date, base_currency, quote_currency, source, reason, http_status, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (date, base_currency, quote_currency, source)
            DO UPDATE SET
                reason = EXCLUDED.reason,
                http_status = EXCLUDED.http_status,
                checked_at = EXCLUDED.checked_at
            """,
            [date, base_currency, quote_currency, source, reason, http_status, native_datetime_utc_now()],
        )

    def get_transient_attempts(self, base_currency: str, source: str) -> dict[datetime.date, int]:
        """Return the consecutive transient-failure count per date.

        :param base_currency:
            Base currency to filter on.
        :param source:
            Provider identifier to filter on.
        :return:
            Mapping of ``date`` to the number of consecutive transient failures
            recorded so far. Dates with no failures are absent.
        """
        rows = self.con.execute(
            """
            SELECT date, transient_attempts FROM fetch_attempts
            WHERE base_currency = ? AND source = ?
            """,
            [base_currency, source],
        ).fetchall()
        return dict(rows)

    def set_transient_attempts(
        self,
        date: datetime.date,
        base_currency: str,
        source: str,
        attempts: int,
    ) -> None:
        """Persist the consecutive transient-failure count for a date.

        :param date:
            Date that failed.
        :param base_currency:
            Base currency code.
        :param source:
            Provider identifier.
        :param attempts:
            New cumulative count of consecutive transient failures.
        """
        self.con.execute(
            """
            INSERT INTO fetch_attempts (
                date, base_currency, source, transient_attempts, last_attempt_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (date, base_currency, source)
            DO UPDATE SET
                transient_attempts = EXCLUDED.transient_attempts,
                last_attempt_at = EXCLUDED.last_attempt_at
            """,
            [date, base_currency, source, attempts, native_datetime_utc_now()],
        )

    def clear_transient_attempts(self, date: datetime.date, base_currency: str, source: str) -> None:
        """Reset the transient-failure counter for a date once it resolves.

        :param date:
            Date that succeeded or was confirmed unavailable.
        :param base_currency:
            Base currency code.
        :param source:
            Provider identifier.
        """
        self.con.execute(
            """
            DELETE FROM fetch_attempts
            WHERE date = ? AND base_currency = ? AND source = ?
            """,
            [date, base_currency, source],
        )

    def _distinct_pairs(self, table: str, base_currency: str, source: str) -> set[tuple[datetime.date, str]]:
        """Return the distinct ``(date, quote_currency)`` pairs in a table.

        :param table:
            Table name (``exchange_rates`` or ``unavailable_rates``); an internal
            constant, never user input.
        :param base_currency:
            Base currency to filter on.
        :param source:
            Provider identifier to filter on.
        :return:
            Set of ``(date, quote_currency)`` tuples.
        """
        rows = self.con.execute(
            f"SELECT date, quote_currency FROM {table} WHERE base_currency = ? AND source = ?",
            [base_currency, source],
        ).fetchall()
        return set(rows)

    def get_present_pairs(self, base_currency: str, source: str) -> set[tuple[datetime.date, str]]:
        """Return ``(date, quote_currency)`` pairs already stored in ``exchange_rates``."""
        return self._distinct_pairs("exchange_rates", base_currency, source)

    def get_unavailable_pairs(self, base_currency: str, source: str) -> set[tuple[datetime.date, str]]:
        """Return ``(date, quote_currency)`` pairs recorded in ``unavailable_rates``."""
        return self._distinct_pairs("unavailable_rates", base_currency, source)

    def row_count(self) -> int:
        """Return the total number of stored exchange rate rows."""
        return self.con.execute("SELECT COUNT(*) FROM exchange_rates").fetchone()[0]

    def get_min_date(self, base_currency: str, source: str) -> datetime.date | None:
        """Return the earliest stored date for a base/source, or ``None`` if empty."""
        return self.con.execute(
            "SELECT MIN(date) FROM exchange_rates WHERE base_currency = ? AND source = ?",
            [base_currency, source],
        ).fetchone()[0]

    def get_max_date(self, base_currency: str, source: str) -> datetime.date | None:
        """Return the latest stored date for a base/source, or ``None`` if empty."""
        return self.con.execute(
            "SELECT MAX(date) FROM exchange_rates WHERE base_currency = ? AND source = ?",
            [base_currency, source],
        ).fetchone()[0]

    def get_rates_dataframe(
        self,
        base_currency: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame:
        """Return stored rates as a DataFrame ordered by date then quote.

        :param base_currency:
            Optional base currency filter.
        :param source:
            Optional source filter.
        :return:
            DataFrame with columns
            ``date, base_currency, quote_currency, rate, source, written_at``.
        """
        clauses = []
        params: list = []
        if base_currency is not None:
            clauses.append("base_currency = ?")
            params.append(base_currency)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"SELECT * FROM exchange_rates {where} ORDER BY date, quote_currency",
            params,
        ).df()
