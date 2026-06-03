"""DuckDB persistence for Core3 risk intelligence data.

Stores project snapshots, PoL time-series, category breakdowns, and
section details. Supports incremental sync using watermarks in the
``sync_state`` table.

Schema
------

Five tables:

- ``project_snapshots`` — one row per poll cycle per project; raw JSON
  payload plus extracted key columns (rank, PoL score, market cap)
- ``section_snapshots`` — optional section detail storage (security,
  financial, etc.)
- ``pol_daily`` — API-native PoL score time-series (sparse timestamps)
- ``pol_category_daily`` — API-native category PoL breakdown time-series
- ``sync_state`` — per-slug watermarks for incremental sync

Thread safety: all database operations are protected by an internal lock.
Multiple threads can call insert methods concurrently — the API calls
run in parallel while database writes are serialised.

Storage location
----------------

Default: ``~/.tradingstrategy/core3/risk-data.duckdb``

Example::

    from pathlib import Path
    from eth_defi.core3.database import Core3Database

    db = Core3Database(Path("/tmp/core3-risk.duckdb"))
    # ... insert data ...
    db.save()
    db.close()
"""

import datetime
import json
import logging
import threading
from pathlib import Path

import pandas as pd

from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


def _unix_ts_to_naive_utc(ts: int | float) -> datetime.datetime:
    """Convert a unix timestamp to a naive UTC datetime.

    Avoids ``datetime.fromtimestamp()`` which applies local timezone,
    and ``datetime.utcfromtimestamp()`` which is deprecated in Python 3.12+.

    :param ts:
        Unix timestamp in seconds.
    :return:
        Naive UTC datetime.
    """
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts))


class Core3Database:
    """DuckDB database for storing Core3 risk intelligence data.

    Stores project snapshots, PoL time-series, category breakdowns,
    and section details. Supports incremental sync using watermarks
    in the ``sync_state`` table.

    Thread safety: all database operations are protected by
    :py:attr:`_db_lock`. Multiple threads can call insert methods
    concurrently — the API calls run in parallel while database
    writes are serialised.

    Example::

        from pathlib import Path
        from eth_defi.core3.database import Core3Database

        db = Core3Database(Path("/tmp/core3-risk.duckdb"))
        db.save()
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
        self._db_lock = threading.Lock()

        # Disable automatic WAL checkpoint (default 16 MiB).
        # When the scanner runs with ThreadPoolExecutor, auto-checkpoint
        # can trigger inside an INSERT while worker threads are alive,
        # causing heap corruption on Python 3.14 + DuckDB 1.5.
        # We do a manual CHECKPOINT via save() after all threads exit.
        # See: https://github.com/duckdb/duckdb/issues/17006
        self.con.execute("SET wal_autocheckpoint = '1TB'")

        self._init_schema()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_schema(self):
        """Create all tables if they don't exist.

        No PRIMARY KEY or UNIQUE constraints are used because DuckDB 1.5.0's
        ART index (used for unique constraint enforcement) causes SIGSEGV
        (heap corruption in ``tiny_malloc_should_clear``) on file-backed
        databases with Python 3.14 + macOS ARM64 when enough rows accumulate.

        Deduplication is handled at the application level using DELETE + INSERT
        instead of ``ON CONFLICT``.

        See `duckdb#17006 <https://github.com/duckdb/duckdb/issues/17006>`__.
        """

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS project_snapshots (
                slug VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                name VARCHAR,
                rank INTEGER,
                pol_score DOUBLE,
                pol_rating VARCHAR,
                market_cap_usd VARCHAR,
                payload VARCHAR NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS section_snapshots (
                slug VARCHAR NOT NULL,
                section VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                section_pol_score DOUBLE,
                payload VARCHAR NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS pol_daily (
                slug VARCHAR NOT NULL,
                ts TIMESTAMP NOT NULL,
                pol_score DOUBLE NOT NULL,
                fetched_at TIMESTAMP NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS pol_category_daily (
                slug VARCHAR NOT NULL,
                ts TIMESTAMP NOT NULL,
                security_score DOUBLE,
                financial_score DOUBLE,
                operational_score DOUBLE,
                reputational_score DOUBLE,
                regulatory_score DOUBLE,
                fetched_at TIMESTAMP NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                slug VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                last_ts BIGINT,
                backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
                last_synced TIMESTAMP NOT NULL
            )
        """)

    def close(self):
        """Close the database connection.

        DuckDB performs an implicit checkpoint on close, flushing
        the WAL to the main database file.
        """
        logger.info("Closing Core3 database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def reconnect(self):
        """Close and reopen the database connection.

        Used to flush writes between chunks when DuckDB CHECKPOINT
        cannot be called safely (e.g. threading issues with
        Python 3.14 + DuckDB 1.5, see `duckdb#13904
        <https://github.com/duckdb/duckdb/issues/13904>`__).

        DuckDB checkpoints implicitly on close, so this is equivalent
        to ``save()`` followed by reopening the connection.
        """
        import duckdb

        if self.con is not None:
            self.con.close()
        self.con = duckdb.connect(str(self.path))
        self.con.execute("SET wal_autocheckpoint = '1TB'")

    def save(self):
        """Force a checkpoint to ensure data is persisted to disk."""
        with self._db_lock:
            if self.con is not None:
                self.con.execute("CHECKPOINT")

    # ------------------------------------------------------------------
    # Insert methods
    # ------------------------------------------------------------------

    def insert_project_snapshot(
        self,
        slug: str,
        fetched_at: datetime.datetime,
        raw_json: dict,
    ) -> None:
        """Insert a project snapshot, extracting key columns from the raw JSON.

        Extracts ``name``, ``rank``, ``pol.score``, ``pol.rating``, and
        ``market_cap.in_usd`` from the raw JSON payload. Stores the full
        JSON as a VARCHAR for future re-extraction if the schema changes.

        :param slug:
            Project slug.
        :param fetched_at:
            Timestamp of when the data was fetched.
        :param raw_json:
            Full JSON response from ``/v1/{slug}``.
        """
        pol = raw_json.get("pol") or {}
        market_cap = raw_json.get("market_cap") or {}
        market_cap_usd = market_cap.get("in_usd")

        with self._db_lock:
            self.con.execute(
                "DELETE FROM project_snapshots WHERE slug = ? AND fetched_at = ?",
                [slug, fetched_at],
            )
            self.con.execute(
                """
                INSERT INTO project_snapshots (slug, fetched_at, name, rank, pol_score, pol_rating, market_cap_usd, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    slug,
                    fetched_at,
                    raw_json.get("name"),
                    raw_json.get("rank"),
                    pol.get("score"),
                    pol.get("rating"),
                    str(market_cap_usd) if market_cap_usd is not None else None,
                    json.dumps(raw_json),
                ],
            )

    def insert_section_snapshot(
        self,
        slug: str,
        section: str,
        fetched_at: datetime.datetime,
        raw_json: dict,
    ) -> None:
        """Insert a section snapshot.

        Extracts ``pol.score`` from the section response as the
        section-level PoL sub-score.

        :param slug:
            Project slug.
        :param section:
            Section name (security, financial, etc.).
        :param fetched_at:
            Fetch timestamp.
        :param raw_json:
            Full section JSON response.
        """
        pol = raw_json.get("pol") or {}

        with self._db_lock:
            self.con.execute(
                "DELETE FROM section_snapshots WHERE slug = ? AND section = ? AND fetched_at = ?",
                [slug, section, fetched_at],
            )
            self.con.execute(
                """
                INSERT INTO section_snapshots (slug, section, fetched_at, section_pol_score, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    slug,
                    section,
                    fetched_at,
                    pol.get("score"),
                    json.dumps(raw_json),
                ],
            )

    def insert_pol_daily_points(
        self,
        slug: str,
        points: list[dict],
        fetched_at: datetime.datetime,
    ) -> int:
        """Insert PoL daily history points, deduplicating on ``(slug, ts)``.

        Converts unix timestamps from the API to naive UTC datetimes.
        Deduplication uses a temp table with DELETE + INSERT instead of
        ``ON CONFLICT`` to avoid DuckDB 1.5.0 ART index crashes.

        :param slug:
            Project slug (or :py:data:`~eth_defi.core3.constants.INDEX_SLUG`
            for the aggregate index).
        :param points:
            List of ``{score, timestamp}`` dicts from the API.
        :param fetched_at:
            When this data was fetched.
        :return:
            Number of new rows inserted.
        """
        if not points:
            return 0

        rows = [(slug, _unix_ts_to_naive_utc(p["timestamp"]), p["score"], fetched_at) for p in points]

        with self._db_lock:
            before = self.con.execute("SELECT COUNT(*) FROM pol_daily WHERE slug = ?", [slug]).fetchone()[0]

            self.con.execute("CREATE TEMP TABLE IF NOT EXISTS _tmp_pol (slug VARCHAR, ts TIMESTAMP, pol_score DOUBLE, fetched_at TIMESTAMP)")
            self.con.execute("DELETE FROM _tmp_pol")
            self.con.executemany("INSERT INTO _tmp_pol VALUES (?, ?, ?, ?)", rows)
            self.con.execute("DELETE FROM pol_daily p USING _tmp_pol t WHERE p.slug = t.slug AND p.ts = t.ts")
            self.con.execute("INSERT INTO pol_daily SELECT * FROM _tmp_pol")

            after = self.con.execute("SELECT COUNT(*) FROM pol_daily WHERE slug = ?", [slug]).fetchone()[0]

        return int(after - before)

    def insert_pol_category_daily_points(
        self,
        slug: str,
        points: list[dict],
        fetched_at: datetime.datetime,
    ) -> int:
        """Insert category PoL daily breakdown points.

        Each point contains a ``timestamp`` and per-category PoL scores
        (``security.score``, ``financial.score``, etc.).

        :param slug:
            Project slug.
        :param points:
            List of point dicts from the category history API.
        :param fetched_at:
            When this data was fetched.
        :return:
            Number of new rows inserted.
        """
        if not points:
            return 0

        rows = []
        for p in points:
            ts = _unix_ts_to_naive_utc(p["timestamp"])
            rows.append(
                (
                    slug,
                    ts,
                    (p.get("security") or {}).get("score"),
                    (p.get("financial") or {}).get("score"),
                    (p.get("operational") or {}).get("score"),
                    (p.get("reputational") or {}).get("score"),
                    (p.get("regulatory") or {}).get("score"),
                    fetched_at,
                )
            )

        with self._db_lock:
            before = self.con.execute("SELECT COUNT(*) FROM pol_category_daily WHERE slug = ?", [slug]).fetchone()[0]

            self.con.execute("""CREATE TEMP TABLE IF NOT EXISTS _tmp_cat (
                slug VARCHAR, ts TIMESTAMP, security_score DOUBLE, financial_score DOUBLE,
                operational_score DOUBLE, reputational_score DOUBLE, regulatory_score DOUBLE,
                fetched_at TIMESTAMP)""")
            self.con.execute("DELETE FROM _tmp_cat")
            self.con.executemany("INSERT INTO _tmp_cat VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.con.execute("DELETE FROM pol_category_daily p USING _tmp_cat t WHERE p.slug = t.slug AND p.ts = t.ts")
            self.con.execute("INSERT INTO pol_category_daily SELECT * FROM _tmp_cat")

            after = self.con.execute("SELECT COUNT(*) FROM pol_category_daily WHERE slug = ?", [slug]).fetchone()[0]

        return int(after - before)

    # ------------------------------------------------------------------
    # Sync state
    # ------------------------------------------------------------------

    def get_sync_state(self, slug: str, data_type: str) -> dict | None:
        """Get sync state for a specific slug and data type.

        :param slug:
            Project slug.
        :param data_type:
            Data type key (e.g. ``'pol_daily'``, ``'pol_category_daily'``).
        :return:
            Dict with ``last_ts``, ``backfill_done``, and ``last_synced``,
            or ``None`` if no state exists.
        """
        with self._db_lock:
            row = self.con.execute(
                "SELECT last_ts, backfill_done, last_synced FROM sync_state WHERE slug = ? AND data_type = ?",
                [slug, data_type],
            ).fetchone()

        if row is None:
            return None
        return {"last_ts": row[0], "backfill_done": row[1], "last_synced": row[2]}

    def update_sync_state(
        self,
        slug: str,
        data_type: str,
        last_ts: int | None,
        backfill_done: bool = True,
    ) -> None:
        """Update or insert sync state watermark.

        Always updates ``last_synced`` to now, even when ``last_ts`` is
        unchanged (zero new points). Sets ``backfill_done=TRUE`` after
        the initial chart backfill.

        When ``backfill_done`` is ``TRUE`` but ``last_ts`` is ``NULL``,
        subsequent runs use incremental with ``from=0`` (epoch), which
        is effectively a full range but avoids re-calling the chart endpoint.

        :param slug:
            Project slug.
        :param data_type:
            Data type key.
        :param last_ts:
            Latest unix timestamp in the synced data, or ``None`` if empty.
        :param backfill_done:
            Whether the initial backfill has been attempted.
        """
        now = native_datetime_utc_now()
        with self._db_lock:
            self.con.execute(
                "DELETE FROM sync_state WHERE slug = ? AND data_type = ?",
                [slug, data_type],
            )
            self.con.execute(
                """
                INSERT INTO sync_state (slug, data_type, last_ts, backfill_done, last_synced)
                VALUES (?, ?, ?, ?, ?)
                """,
                [slug, data_type, last_ts, backfill_done, now],
            )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_latest_project_snapshots(self) -> pd.DataFrame:
        """Get the most recent snapshot for each project.

        :return:
            DataFrame with the latest snapshot per slug, ordered by rank.
        """
        with self._db_lock:
            return self.con.execute("""
                    SELECT ps.*
                    FROM project_snapshots ps
                    INNER JOIN (
                        SELECT slug, MAX(fetched_at) AS max_fetched_at
                        FROM project_snapshots
                        GROUP BY slug
                    ) latest ON ps.slug = latest.slug AND ps.fetched_at = latest.max_fetched_at
                    ORDER BY ps.rank NULLS LAST
                """).df()

    def get_project_snapshot_history(self, slug: str) -> pd.DataFrame:
        """Get all snapshots for a specific project over time.

        :param slug:
            Project slug.
        :return:
            DataFrame ordered by fetched_at.
        """
        with self._db_lock:
            return self.con.execute(
                "SELECT * FROM project_snapshots WHERE slug = ? ORDER BY fetched_at",
                [slug],
            ).df()

    def get_pol_daily(self, slug: str) -> pd.DataFrame:
        """Get daily PoL time-series for a project.

        :param slug:
            Project slug (or :py:data:`~eth_defi.core3.constants.INDEX_SLUG`
            for the aggregate).
        :return:
            DataFrame with ``ts`` and ``pol_score`` columns, ordered by ``ts``.
        """
        with self._db_lock:
            return self.con.execute(
                "SELECT * FROM pol_daily WHERE slug = ? ORDER BY ts",
                [slug],
            ).df()

    def get_pol_category_daily(self, slug: str) -> pd.DataFrame:
        """Get daily category PoL breakdown for a project.

        :param slug:
            Project slug.
        :return:
            DataFrame with ``ts`` and category score columns.
        """
        with self._db_lock:
            return self.con.execute(
                "SELECT * FROM pol_category_daily WHERE slug = ? ORDER BY ts",
                [slug],
            ).df()

    def get_project_count(self) -> int:
        """Get number of unique projects in the database.

        :return:
            Count of unique slugs in ``project_snapshots``.
        """
        with self._db_lock:
            return int(self.con.execute("SELECT COUNT(DISTINCT slug) FROM project_snapshots").fetchone()[0])

    def get_snapshot_count(self) -> int:
        """Get total number of project snapshot records.

        :return:
            Total count of rows in ``project_snapshots``.
        """
        with self._db_lock:
            return int(self.con.execute("SELECT COUNT(*) FROM project_snapshots").fetchone()[0])

    def get_pol_daily_count(self) -> int:
        """Get total number of PoL daily records.

        :return:
            Total count of rows in ``pol_daily``.
        """
        with self._db_lock:
            return int(self.con.execute("SELECT COUNT(*) FROM pol_daily").fetchone()[0])
