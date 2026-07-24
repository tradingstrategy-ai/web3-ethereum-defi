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

Default: ``~/.tradingstrategy/vaults/core3/core3.duckdb``

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
import os
import tempfile
import threading
from pathlib import Path

import duckdb
import pandas as pd

from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


#: DuckDB storage version for new Core3 databases.
#:
#: ``latest`` enables native Zstandard compression for ``VARCHAR`` columns.
#: The resulting file requires its writer's DuckDB version or a newer version.
CORE3_DUCKDB_STORAGE_COMPATIBILITY_VERSION = "latest"

#: Core3 tables owned by :class:`Core3Database` and copied during migration.
CORE3_DATABASE_TABLES = (
    "project_snapshots",
    "section_snapshots",
    "pol_daily",
    "pol_category_daily",
    "sync_state",
)

#: Per-table storage inspection queries for compressed Core3 JSON payloads.
CORE3_PAYLOAD_COMPRESSION_QUERIES = {
    "project_snapshots": """
        SELECT DISTINCT compression
        FROM pragma_storage_info('project_snapshots')
        WHERE column_name = 'payload'
            AND segment_type = 'VARCHAR'
            AND persistent
    """,
    "section_snapshots": """
        SELECT DISTINCT compression
        FROM pragma_storage_info('section_snapshots')
        WHERE column_name = 'payload'
            AND segment_type = 'VARCHAR'
            AND persistent
    """,
}


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

        self.path = path
        if path.exists() and path.stat().st_size > 0:
            self._migrate_to_latest_storage_format(path)

        self.con = duckdb.connect(
            str(path),
            config={"storage_compatibility_version": CORE3_DUCKDB_STORAGE_COMPATIBILITY_VERSION},
        )
        self._db_lock = threading.Lock()

        # Disable automatic WAL checkpoint (default 16 MiB).
        # DuckDB 1.5.0 has ART index heap corruption on file-backed DBs
        # with Python 3.14 + macOS ARM64. We removed PRIMARY KEY constraints
        # as a workaround, but also disable auto-checkpoint as a defensive
        # measure. Manual CHECKPOINT via save() after all writes complete.
        # See: https://github.com/duckdb/duckdb/issues/17006
        self.con.execute("SET wal_autocheckpoint = '1TB'")

        self._init_schema(self.con)

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    @staticmethod
    def _init_schema(connection: duckdb.DuckDBPyConnection) -> None:
        """Create the Core3 schema with compressed raw JSON payloads.

        No PRIMARY KEY or UNIQUE constraints are used because DuckDB 1.5.0's
        ART index (used for unique constraint enforcement) causes SIGSEGV
        (heap corruption in ``tiny_malloc_should_clear``) on file-backed
        databases with Python 3.14 + macOS ARM64 when enough rows accumulate.

        Deduplication is handled at the application level using DELETE + INSERT
        instead of ``ON CONFLICT``.

        See `duckdb#17006 <https://github.com/duckdb/duckdb/issues/17006>`__.

        :param connection:
            DuckDB connection for either the live database or a migration target.
        """

        connection.execute("""
            CREATE TABLE IF NOT EXISTS project_snapshots (
                slug VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                name VARCHAR,
                rank INTEGER,
                pol_score DOUBLE,
                pol_rating VARCHAR,
                market_cap_usd VARCHAR,
                payload VARCHAR USING COMPRESSION 'zstd' NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS section_snapshots (
                slug VARCHAR NOT NULL,
                section VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                section_pol_score DOUBLE,
                payload VARCHAR USING COMPRESSION 'zstd' NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS pol_daily (
                slug VARCHAR NOT NULL,
                ts TIMESTAMP NOT NULL,
                pol_score DOUBLE NOT NULL,
                fetched_at TIMESTAMP NOT NULL
            )
        """)

        connection.execute("""
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

        connection.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                slug VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                last_ts BIGINT,
                backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
                last_synced TIMESTAMP NOT NULL
            )
        """)

    @staticmethod
    def _fetch_storage_version(path: Path) -> str:
        """Read the DuckDB storage format used by a database file.

        DuckDB exposes the format through the ``storage_version`` database tag.
        The tag is more reliable than the running DuckDB version because old
        database files remain readable after the Python package is upgraded.

        :param path:
            Existing DuckDB database file to inspect.
        :return:
            Storage format tag, such as ``v1.0.0+``.
        """
        connection = duckdb.connect(str(path), read_only=True)
        try:
            tags = connection.execute("SELECT tags FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
        finally:
            connection.close()

        return tags["storage_version"]

    @staticmethod
    def _uses_latest_storage_format(storage_version: str) -> bool:
        """Check whether a database uses this DuckDB version's latest format.

        DuckDB releases intentionally retain an old default format for forward
        compatibility. Comparing the recorded storage tag makes a newer
        package upgrade trigger exactly one Core3 migration.

        :param storage_version:
            Database ``storage_version`` tag returned by DuckDB.
        :return:
            ``True`` when the file already uses the installed format.
        """
        major, minor, _patch = duckdb.__version__.split(".", maxsplit=2)
        return storage_version == f"v{major}.{minor}.0+"

    @classmethod
    def _migrate_to_latest_storage_format(cls, path: Path) -> None:
        """Rebuild a legacy Core3 database in the latest DuckDB format.

        The migration writes a sibling database with Zstandard-compressed JSON
        columns, checks every Core3 table row count and then atomically replaces
        the original file. The original remains untouched until the target has
        been checkpointed and verified.

        :param path:
            Existing Core3 DuckDB database file to migrate.
        :return:
            ``None``. The migrated database replaces ``path`` on success.
        """
        storage_version = cls._fetch_storage_version(path)
        if cls._uses_latest_storage_format(storage_version):
            return

        # Replay any recovery WAL before attaching the source read-only. This
        # guarantees that the replacement cannot leave an old sidecar WAL next
        # to the newly migrated main database file.
        source_connection = duckdb.connect(str(path))
        try:
            source_connection.execute("CHECKPOINT")
        finally:
            source_connection.close()

        logger.info(
            "Migrating Core3 database at %s from DuckDB storage format %s to %s",
            path,
            storage_version,
            CORE3_DUCKDB_STORAGE_COMPATIBILITY_VERSION,
        )

        file_descriptor, migration_path_str = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.migration-",
            suffix=".duckdb",
        )
        os.close(file_descriptor)
        migration_path = Path(migration_path_str)
        migration_path.unlink()
        replaced = False

        try:
            migration = duckdb.connect(
                str(migration_path),
                config={"storage_compatibility_version": CORE3_DUCKDB_STORAGE_COMPATIBILITY_VERSION},
            )
            try:
                quoted_path = str(path).replace("'", "''")
                migration.execute(f"ATTACH '{quoted_path}' AS source (READ_ONLY)")
                cls._init_schema(migration)

                source_tables = {
                    row[0]
                    for row in migration.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_catalog = 'source'
                            AND table_schema = 'main'
                        """
                    ).fetchall()
                }
                for table_name in CORE3_DATABASE_TABLES:
                    if table_name in source_tables:
                        # Table names are fixed module constants, not user input.
                        migration.execute(f"INSERT INTO {table_name} SELECT * FROM source.{table_name}")  # noqa: S608

                cls._validate_migration(migration, source_tables)
                migration.execute("CHECKPOINT")
            finally:
                migration.close()

            cls._validate_migrated_file(migration_path, source_tables)
            os.replace(migration_path, path)
            replaced = True
        finally:
            if not replaced and migration_path.exists():
                migration_path.unlink()

        logger.info("Completed Core3 database migration at %s", path)

    @staticmethod
    def _validate_migration(connection: duckdb.DuckDBPyConnection, source_tables: set[str]) -> None:
        """Check that an attached source database was copied without data loss.

        Validating before replacing the source protects the on-disk historical
        risk database from a schema or copy regression. Only known Core3 tables
        are compared because this handler owns their schema.

        :param connection:
            Migration connection with the original database attached as ``source``.
        :param source_tables:
            Tables present in the source database's main schema.
        :return:
            ``None``. Raises :class:`RuntimeError` if validation fails.
        """
        for table_name in CORE3_DATABASE_TABLES:
            if table_name not in source_tables:
                continue

            # Table names are fixed module constants, not user input.
            source_count = connection.execute(f"SELECT COUNT(*) FROM source.{table_name}").fetchone()[0]  # noqa: S608
            migrated_count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]  # noqa: S608
            if source_count != migrated_count:
                raise RuntimeError(f"Core3 database migration copied {migrated_count} rows for {table_name}, expected {source_count}")

    @classmethod
    def _validate_migrated_file(cls, path: Path, source_tables: set[str]) -> None:
        """Verify the checkpointed migration target and its Zstd payload columns.

        DuckDB must be able to reopen the replacement file before the atomic
        swap. Non-empty payload tables must additionally report ``ZSTD`` in
        ``pragma_storage_info`` so a future schema change cannot silently lose
        the intended disk saving.

        :param path:
            Checkpointed migration target to reopen read-only.
        :param source_tables:
            Tables present in the original Core3 database.
        :return:
            ``None``. Raises :class:`RuntimeError` if compression is absent.
        """
        connection = duckdb.connect(str(path), read_only=True)
        try:
            storage_version = connection.execute("SELECT tags['storage_version'] FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
            if not cls._uses_latest_storage_format(storage_version):
                raise RuntimeError(f"Core3 database migration wrote unexpected storage format {storage_version}")

            for table_name in ("project_snapshots", "section_snapshots"):
                if table_name not in source_tables:
                    continue

                # Table names are fixed module constants, not user input.
                row_count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]  # noqa: S608
                if row_count == 0:
                    continue

                compressions = {row[0] for row in connection.execute(CORE3_PAYLOAD_COMPRESSION_QUERIES[table_name]).fetchall()}
                if compressions != {"ZSTD"}:
                    raise RuntimeError(f"Core3 database migration did not Zstd-compress {table_name}.payload: {compressions}")
        finally:
            connection.close()

    def close(self):
        """Close the database connection.

        DuckDB performs an implicit checkpoint on close, flushing
        the WAL to the main database file.
        """
        logger.info("Closing Core3 database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

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

    def get_latest_pol_category(self, slug: str) -> dict | None:
        """Get the latest per-category PoL breakdown for a project.

        Returns the most recent row from ``pol_category_daily``, which
        holds the API-native sub-scores for the five Core3 risk
        categories (security, financial, operational, reputational,
        regulatory). Used to embed the category breakdown in the vault
        metrics JSON export.

        :param slug:
            Core3 project slug.

        :return:
            Dict with ``ts`` (naive UTC :class:`~datetime.datetime`) and
            ``security``, ``financial``, ``operational``,
            ``reputational``, ``regulatory`` float sub-scores (each may
            be ``None``), or ``None`` if the slug has no category rows.
        """
        with self._db_lock:
            row = self.con.execute(
                """
                SELECT ts, security_score, financial_score, operational_score, reputational_score, regulatory_score
                FROM pol_category_daily
                WHERE slug = ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                [slug],
            ).fetchone()
        if row is None:
            return None
        return {
            "ts": row[0],
            "security": row[1],
            "financial": row[2],
            "operational": row[3],
            "reputational": row[4],
            "regulatory": row[5],
        }

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

    def get_latest_project_snapshot_raw(self, slug: str) -> tuple[str, "datetime.datetime"] | None:
        """Get the raw JSON payload and fetch timestamp for the latest snapshot of a project.

        :param slug:
            Core3 project slug.
        :return:
            Tuple of ``(payload_json_string, fetched_at)`` or ``None``
            if the slug has no snapshots.
        """
        with self._db_lock:
            row = self.con.execute(
                """
                SELECT payload, fetched_at
                FROM project_snapshots
                WHERE slug = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                [slug],
            ).fetchone()
        if row is None:
            return None
        return row[0], row[1]
