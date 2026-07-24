"""DuckDB persistence for vault post tracking."""

import datetime
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import duckdb
import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.sources import TrackedPostSource

logger = logging.getLogger(__name__)


#: Default DuckDB path for collected vault posts.
DEFAULT_VAULT_POST_DATABASE = Path("~/.tradingstrategy/vaults/vault-post-database.duckdb").expanduser()


#: DuckDB storage version for new vault-post databases.
#:
#: ``latest`` enables native Zstandard compression for long post text and raw
#: source API payloads. The resulting file requires its writer's DuckDB
#: version or a newer version.
VAULT_POST_DUCKDB_STORAGE_COMPATIBILITY_VERSION = "latest"

#: Vault-post tables owned by :class:`VaultPostDatabase` and copied during migration.
VAULT_POST_DATABASE_TABLES = (
    "tracked_sources",
    "posts",
    "feed_sync_state",
)

#: Columns owned by each vault-post table, in the migration target's order.
#:
#: Existing database files can predate a nullable column added through
#: ``ALTER TABLE``.  Migration copies by these names rather than source column
#: position, preserving the historical schema variants.
VAULT_POST_DATABASE_COLUMNS = {
    "tracked_sources": (
        "source_id",
        "feeder_id",
        "name",
        "role",
        "website",
        "source_type",
        "source_key",
        "canonical_url",
        "last_checked_at",
        "last_success_at",
        "last_error",
        "last_post_published_at",
        "added_at",
        "updated_at",
    ),
    "posts": (
        "source_id",
        "external_post_id",
        "title",
        "post_url",
        "published_at",
        "fetched_at",
        "short_description",
        "full_text",
        "ai_summary",
        "raw_payload",
    ),
    "feed_sync_state": (
        "key",
        "value",
        "updated_at",
    ),
}

#: Nullable columns added after the original post-scanner schema.
VAULT_POST_OPTIONAL_MIGRATION_COLUMNS = frozenset({"website", "raw_payload"})

#: Per-column storage inspection queries for Zstandard-compressed post content.
VAULT_POST_COMPRESSION_QUERIES = {
    "full_text": """
        SELECT DISTINCT compression
        FROM pragma_storage_info('posts')
        WHERE column_name = 'full_text'
            AND segment_type = 'VARCHAR'
            AND persistent
    """,
    "raw_payload": """
        SELECT DISTINCT compression
        FROM pragma_storage_info('posts')
        WHERE column_name = 'raw_payload'
            AND segment_type = 'VARCHAR'
            AND persistent
    """,
}


def resolve_feed_database_path() -> Path:
    """Resolve the vault post feed DuckDB database path.

    Mirrors the resolution used by the post scanner
    (``scripts/erc-4626/scan-vault-posts.py``) so the JSON export reads
    the same database the feed collector writes.  Keeping the resolver
    next to :py:data:`DEFAULT_VAULT_POST_DATABASE` avoids each caller
    repeating the same environment lookup and path expansion logic.

    The ``FEED_DB_PATH`` override takes precedence, falling back to the
    ``DB_PATH`` variable consumed by the post scanner, then the default
    path.

    :return:
        Path from ``FEED_DB_PATH``, then ``DB_PATH``, then the default
        vault post database path.
    """
    path = os.environ.get("FEED_DB_PATH") or os.environ.get("DB_PATH")
    return Path(path).expanduser() if path else DEFAULT_VAULT_POST_DATABASE


@dataclass(slots=True, frozen=True)
class CollectedPost:
    """A single normalised post ready for database insertion."""

    #: Stable external identifier derived from the feed entry or a deterministic fallback.
    external_post_id: str
    #: Entry title when the feed provides one.
    title: str | None
    #: Canonical link to the source post when available.
    post_url: str | None
    #: Original post publication timestamp in naive UTC.
    published_at: datetime.datetime | None
    #: Timestamp when the collector fetched this post in naive UTC.
    fetched_at: datetime.datetime
    #: Short preview text stored alongside the post.
    #:
    #: Capped at 200 characters for compact listings; this is **not** the
    #: complete post body.  See :py:attr:`full_text` for the full content.
    short_description: str
    #: Best available full text extracted from the feed entry.
    #:
    #: For X/Twitter this is the complete *note tweet* body for tweets longer
    #: than 280 characters, populated via
    #: :py:func:`eth_defi.feed.twitter_api._extract_full_tweet_text`.  See
    #: :py:attr:`short_description` for the truncated preview.
    full_text: str
    #: Optional future AI-generated summary, null in the current version.
    ai_summary: str | None = None
    #: JSON-serialised raw payload from the source API (e.g. full tweet object from X API).
    raw_payload: str | None = None


class VaultPostDatabase:
    """DuckDB database for tracked sources and collected posts."""

    def __init__(self, path: Path):
        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        path.parent.mkdir(parents=True, exist_ok=True)

        self.path = path
        if path.exists() and path.stat().st_size > 0:
            self._migrate_to_latest_storage_format(path)

        self.con = duckdb.connect(
            str(path),
            config={"storage_compatibility_version": VAULT_POST_DUCKDB_STORAGE_COMPATIBILITY_VERSION},
        )
        self._init_schema(self.con)

    def __enter__(self) -> Self:
        """Enter a context-managed database session."""

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the database at the end of a context-managed session."""

        self.close()

    @staticmethod
    def _init_schema(connection: duckdb.DuckDBPyConnection, sequence_start: int = 1) -> None:
        """Create the vault-post schema with compressed text payloads.

        ``sequence_start`` is used only while migrating an existing database.
        DuckDB's latest storage format makes the ``source_id`` default depend
        on its sequence, so the old drop-and-recreate sequence reset cannot be
        used after the table exists.

        :param connection:
            DuckDB connection for either the live database or a migration target.
        :param sequence_start:
            First identifier allocated by the tracked-source sequence.
        """
        assert sequence_start > 0, f"Expected positive sequence start, got {sequence_start}"

        connection.execute(f"CREATE SEQUENCE IF NOT EXISTS tracked_sources_id_seq START {sequence_start}")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS tracked_sources (
                source_id BIGINT PRIMARY KEY DEFAULT nextval('tracked_sources_id_seq'),
                feeder_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                website VARCHAR,
                source_type VARCHAR NOT NULL,
                source_key VARCHAR NOT NULL,
                canonical_url VARCHAR NOT NULL,
                last_checked_at TIMESTAMP,
                last_success_at TIMESTAMP,
                last_error VARCHAR,
                last_post_published_at TIMESTAMP,
                added_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE (feeder_id, role, source_type, source_key)
            )
        """)
        connection.execute("ALTER TABLE tracked_sources ADD COLUMN IF NOT EXISTS website VARCHAR")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                source_id BIGINT NOT NULL,
                external_post_id VARCHAR NOT NULL,
                title VARCHAR,
                post_url VARCHAR,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP NOT NULL,
                short_description VARCHAR NOT NULL,
                full_text VARCHAR USING COMPRESSION 'zstd' NOT NULL,
                ai_summary VARCHAR,
                raw_payload VARCHAR USING COMPRESSION 'zstd',
                PRIMARY KEY (source_id, external_post_id)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS feed_sync_state (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)

    @staticmethod
    def _fetch_storage_version(path: Path) -> str:
        """Read the DuckDB storage format used by a database file.

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

        :param storage_version:
            Database ``storage_version`` tag returned by DuckDB.
        :return:
            ``True`` when the file already uses the installed format.
        """
        major, minor, _patch = duckdb.__version__.split(".", maxsplit=2)
        return storage_version == f"v{major}.{minor}.0+"

    @classmethod
    def _migrate_to_latest_storage_format(cls, path: Path) -> None:
        """Rebuild a legacy vault-post database in the latest DuckDB format.

        The migration writes a sibling database with Zstandard-compressed post
        body and raw-payload columns, verifies all owned table row counts and
        then atomically replaces the original. The original remains untouched
        until the target has been checkpointed and verified.

        :param path:
            Existing vault-post DuckDB database file to migrate.
        :return:
            ``None``. The migrated database replaces ``path`` on success.
        """
        storage_version = cls._fetch_storage_version(path)
        if cls._uses_latest_storage_format(storage_version):
            return

        # Replay any recovery WAL before attaching the source read-only. This
        # prevents an old sidecar WAL from being left next to the replacement.
        source_connection = duckdb.connect(str(path))
        try:
            source_connection.execute("CHECKPOINT")
        finally:
            source_connection.close()

        logger.info(
            "Migrating vault-post database at %s from DuckDB storage format %s to %s",
            path,
            storage_version,
            VAULT_POST_DUCKDB_STORAGE_COMPATIBILITY_VERSION,
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
                config={"storage_compatibility_version": VAULT_POST_DUCKDB_STORAGE_COMPATIBILITY_VERSION},
            )
            try:
                quoted_path = str(path).replace("'", "''")
                migration.execute(f"ATTACH '{quoted_path}' AS source (READ_ONLY)")
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
                source_max_id = cls._fetch_source_max_id(migration, source_tables)
                cls._init_schema(migration, sequence_start=source_max_id + 1)

                for table_name in VAULT_POST_DATABASE_TABLES:
                    if table_name in source_tables:
                        cls._copy_source_table(migration, table_name)

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

        logger.info("Completed vault-post database migration at %s", path)

    @staticmethod
    def _copy_source_table(connection: duckdb.DuckDBPyConnection, table_name: str) -> None:
        """Copy an attached source table by name, including legacy nullable defaults.

        Both ``website`` and ``raw_payload`` were introduced with ``ALTER
        TABLE``.  Older database files therefore lack these columns, while
        newer files have them appended to the source table.  Selecting named
        columns avoids a positional schema mismatch and supplies ``NULL`` for
        either missing nullable column.

        :param connection:
            Migration connection with the original database attached as ``source``.
        :param table_name:
            A fixed vault-post table name from :data:`VAULT_POST_DATABASE_TABLES`.
        """
        target_columns = VAULT_POST_DATABASE_COLUMNS[table_name]
        source_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_catalog = 'source'
                    AND table_schema = 'main'
                    AND table_name = ?
                """,
                [table_name],
            ).fetchall()
        }
        missing_required_columns = set(target_columns) - source_columns - VAULT_POST_OPTIONAL_MIGRATION_COLUMNS
        if missing_required_columns:
            raise RuntimeError(f"Vault-post database table {table_name} is missing required columns: {sorted(missing_required_columns)}")
        extra_source_columns = source_columns - set(target_columns)
        if extra_source_columns:
            raise RuntimeError(f"Vault-post database table {table_name} has unrecognised columns: {sorted(extra_source_columns)}")

        select_expressions = [column_name if column_name in source_columns else f"NULL AS {column_name}" for column_name in target_columns]
        columns_sql = ", ".join(target_columns)
        select_sql = ", ".join(select_expressions)
        # Table and column names originate only from module constants.
        connection.execute(f"INSERT INTO {table_name} ({columns_sql}) SELECT {select_sql} FROM source.{table_name}")  # noqa: S608

    @staticmethod
    def _fetch_source_max_id(connection: duckdb.DuckDBPyConnection, source_tables: set[str]) -> int:
        """Read the maximum source ID from the attached migration source.

        :param connection:
            Migration connection with the original database attached as ``source``.
        :param source_tables:
            Tables present in the source database's main schema.
        :return:
            Largest source identifier, or zero when the source has no table or rows.
        """
        if "tracked_sources" not in source_tables:
            return 0

        return int(connection.execute("SELECT COALESCE(MAX(source_id), 0) FROM source.tracked_sources").fetchone()[0])

    @staticmethod
    def _validate_migration(connection: duckdb.DuckDBPyConnection, source_tables: set[str]) -> None:
        """Check that an attached source database was copied without data loss.

        :param connection:
            Migration connection with the original database attached as ``source``.
        :param source_tables:
            Tables present in the source database's main schema.
        :return:
            ``None``. Raises :class:`RuntimeError` if validation fails.
        """
        for table_name in VAULT_POST_DATABASE_TABLES:
            if table_name not in source_tables:
                continue

            # Table names are fixed module constants, not user input.
            source_count = connection.execute(f"SELECT COUNT(*) FROM source.{table_name}").fetchone()[0]  # noqa: S608
            migrated_count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]  # noqa: S608
            if source_count != migrated_count:
                raise RuntimeError(f"Vault-post database migration copied {migrated_count} rows for {table_name}, expected {source_count}")

    @classmethod
    def _validate_migrated_file(cls, path: Path, source_tables: set[str]) -> None:
        """Verify the checkpointed target and its Zstandard post-content columns.

        :param path:
            Checkpointed migration target to reopen read-only.
        :param source_tables:
            Tables present in the original vault-post database.
        :return:
            ``None``. Raises :class:`RuntimeError` if compression is absent.
        """
        connection = duckdb.connect(str(path), read_only=True)
        try:
            storage_version = connection.execute("SELECT tags['storage_version'] FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
            if not cls._uses_latest_storage_format(storage_version):
                raise RuntimeError(f"Vault-post database migration wrote unexpected storage format {storage_version}")

            if "posts" not in source_tables:
                return

            for column_name, query in VAULT_POST_COMPRESSION_QUERIES.items():
                # Column names are fixed module constants, not user input.
                row_count = connection.execute(f"SELECT COUNT({column_name}) FROM posts").fetchone()[0]  # noqa: S608
                if row_count == 0:
                    continue

                compressions = {row[0] for row in connection.execute(query).fetchall()}
                if compressions != {"ZSTD"}:
                    raise RuntimeError(f"Vault-post database migration did not Zstd-compress posts.{column_name}: {compressions}")
        finally:
            connection.close()

    def close(self) -> None:
        """Close the database connection."""

        logger.info("Closing vault post database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def save(self) -> None:
        """Force a checkpoint."""

        if self.con is not None:
            self.con.execute("CHECKPOINT")

    def upsert_tracked_source(self, source: TrackedPostSource) -> int:
        """Insert or update one tracked source and return its source ID."""

        existing = self.con.execute(
            """
            SELECT source_id
            FROM tracked_sources
            WHERE feeder_id = ?
              AND role = ?
              AND source_type = ?
              AND source_key = ?
            """,
            [
                source.feeder_id,
                source.role,
                source.source_type,
                source.source_key,
            ],
        ).fetchone()

        now_ = native_datetime_utc_now()
        if existing:
            source_id = int(existing[0])
            self.con.execute(
                """
                UPDATE tracked_sources
                SET name = ?,
                    website = ?,
                    canonical_url = ?,
                    updated_at = ?
                WHERE source_id = ?
                """,
                [
                    source.name,
                    source.website,
                    source.canonical_url,
                    now_,
                    source_id,
                ],
            )
            return source_id

        source_id = int(self.con.execute("SELECT nextval('tracked_sources_id_seq')").fetchone()[0])
        self.con.execute(
            """
            INSERT INTO tracked_sources (
                source_id,
                feeder_id,
                name,
                role,
                website,
                source_type,
                source_key,
                canonical_url,
                added_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                source.feeder_id,
                source.name,
                source.role,
                source.website,
                source.source_type,
                source.source_key,
                source.canonical_url,
                now_,
                now_,
            ],
        )
        return source_id

    def upsert_tracked_sources(self, sources: Iterable[TrackedPostSource]) -> dict[tuple[str, str, str, str], int]:
        """Insert or update tracked sources and return source IDs by logical key."""

        result = {}
        for source in sources:
            result[source.get_logical_key()] = self.upsert_tracked_source(source)
        return result

    def mark_source_success(
        self,
        source_id: int,
        *,
        checked_at: datetime.datetime | None = None,
        last_post_published_at: datetime.datetime | None = None,
    ) -> None:
        """Update sync state for a successful source fetch."""

        checked_at = checked_at or native_datetime_utc_now()
        self.con.execute(
            """
            UPDATE tracked_sources
            SET last_checked_at = ?,
                last_success_at = ?,
                last_error = NULL,
                last_post_published_at = CASE
                    WHEN ? IS NULL THEN last_post_published_at
                    WHEN last_post_published_at IS NULL THEN ?
                    ELSE GREATEST(last_post_published_at, ?)
                END
            WHERE source_id = ?
            """,
            [
                checked_at,
                checked_at,
                last_post_published_at,
                last_post_published_at,
                last_post_published_at,
                source_id,
            ],
        )

    def mark_source_failure(
        self,
        source_id: int,
        error: str,
        *,
        checked_at: datetime.datetime | None = None,
    ) -> None:
        """Update sync state for a failed or skipped source fetch."""

        checked_at = checked_at or native_datetime_utc_now()
        self.con.execute(
            """
            UPDATE tracked_sources
            SET last_checked_at = ?,
                last_error = ?
            WHERE source_id = ?
            """,
            [
                checked_at,
                error,
                source_id,
            ],
        )

    def insert_posts(self, source_id: int, posts: Iterable[CollectedPost]) -> int:
        """Insert posts for a source and return the number of new rows."""

        rows = list(posts)
        if not rows:
            return 0

        before_count = int(self.con.execute("SELECT COUNT(*) FROM posts WHERE source_id = ?", [source_id]).fetchone()[0])
        self.con.executemany(
            """
            INSERT INTO posts (
                source_id,
                external_post_id,
                title,
                post_url,
                published_at,
                fetched_at,
                short_description,
                full_text,
                ai_summary,
                raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_id, external_post_id) DO NOTHING
            """,
            [
                (
                    source_id,
                    post.external_post_id,
                    post.title,
                    post.post_url,
                    post.published_at,
                    post.fetched_at,
                    post.short_description,
                    post.full_text,
                    post.ai_summary,
                    post.raw_payload,
                )
                for post in rows
            ],
        )
        after_count = int(self.con.execute("SELECT COUNT(*) FROM posts WHERE source_id = ?", [source_id]).fetchone()[0])
        return after_count - before_count

    def prune_posts(self, max_post_age_days: int) -> int:
        """Delete posts older than the configured retention period."""

        cutoff = native_datetime_utc_now() - datetime.timedelta(days=max_post_age_days)
        to_delete = int(
            self.con.execute(
                """
                SELECT COUNT(*)
                FROM posts
                WHERE COALESCE(published_at, fetched_at) < ?
                """,
                [cutoff],
            ).fetchone()[0]
        )
        if to_delete:
            self.con.execute(
                """
                DELETE FROM posts
                WHERE COALESCE(published_at, fetched_at) < ?
                """,
                [cutoff],
            )
        return to_delete

    def get_sync_state(self, key: str) -> str | None:
        """Read a value from the feed_sync_state table."""

        row = self.con.execute(
            "SELECT value FROM feed_sync_state WHERE key = ?",
            [key],
        ).fetchone()
        return row[0] if row else None

    def set_sync_state(self, key: str, value: str) -> None:
        """Write a value to the feed_sync_state table."""

        now_ = native_datetime_utc_now()
        self.con.execute(
            """
            INSERT INTO feed_sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value = ?, updated_at = ?
            """,
            [key, value, now_, value, now_],
        )

    def get_known_post_ids(self, source_id: int | None = None) -> set[str]:
        """Return all known external_post_id values, optionally filtered by source."""

        if source_id is not None:
            rows = self.con.execute(
                "SELECT external_post_id FROM posts WHERE source_id = ?",
                [source_id],
            ).fetchall()
        else:
            rows = self.con.execute("SELECT external_post_id FROM posts").fetchall()
        return {row[0] for row in rows}

    def get_source_last_post_timestamps(self, source_ids: Iterable[int]) -> dict[int, datetime.datetime | None]:
        """Return the stored ``last_post_published_at`` for the given source IDs.

        Used to gate backfill fallbacks: a source whose stored timestamp is
        not ``None`` has already been seen before and does not need a fallback
        individual timeline read.

        :param source_ids:
            Iterable of numeric source IDs to look up.

        :return:
            Mapping of ``source_id → last_post_published_at`` (``None`` when
            the column has never been set for that row).
        """

        ids = list(source_ids)
        if not ids:
            return {}
        placeholders = ", ".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT source_id, last_post_published_at FROM tracked_sources WHERE source_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {int(row[0]): row[1] for row in rows}

    def get_tracked_sources_df(self) -> pd.DataFrame:
        """Return tracked source rows for diagnostics."""

        return self.con.execute("SELECT * FROM tracked_sources ORDER BY source_id").df()

    def get_posts_df(self) -> pd.DataFrame:
        """Return stored posts for diagnostics."""

        return self.con.execute("SELECT * FROM posts ORDER BY source_id, COALESCE(published_at, fetched_at)").df()

    def fetch_recent_posts_by_feeder(
        self,
        feeder_ids: Iterable[str],
        max_per_feeder: int = 10,
    ) -> dict[str, list[dict]]:
        """Fetch the most recent posts for each feeder across all source types.

        Joins ``tracked_sources`` and ``posts`` on ``source_id``, ranks
        posts per feeder by ``COALESCE(published_at, fetched_at) DESC``,
        and returns the *max_per_feeder* newest posts per feeder.

        :param feeder_ids:
            Iterable of feeder-id slugs to look up.

        :param max_per_feeder:
            Maximum number of posts to return per feeder.

        :return:
            Dict mapping ``feeder_id`` to a list of post dicts with keys
            ``title``, ``short_description``, ``full_text``, ``post_url``,
            ``source_type``, ``published_at`` (always set via COALESCE
            fallback to ``fetched_at``).  Lists are ordered newest-first.
        """
        ids = list(feeder_ids)
        if not ids:
            return {}

        placeholders = ", ".join("?" * len(ids))
        rows = self.con.execute(
            f"""
            WITH ranked AS (
                SELECT
                    ts.feeder_id,
                    p.title,
                    p.short_description,
                    p.full_text,
                    p.post_url,
                    ts.source_type,
                    COALESCE(p.published_at, p.fetched_at) AS published_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY ts.feeder_id
                        ORDER BY COALESCE(p.published_at, p.fetched_at) DESC
                    ) AS rn
                FROM tracked_sources ts
                JOIN posts p ON ts.source_id = p.source_id
                WHERE ts.feeder_id IN ({placeholders})
            )
            SELECT feeder_id, title, short_description, full_text, post_url, source_type, published_at
            FROM ranked
            WHERE rn <= ?
            ORDER BY feeder_id, published_at DESC
            """,
            ids + [max_per_feeder],
        ).fetchall()

        result: dict[str, list[dict]] = {}
        for row in rows:
            feeder_id, title, short_description, full_text, post_url, source_type, published_at = row
            result.setdefault(feeder_id, []).append(
                {
                    "title": title,
                    "short_description": short_description,
                    "full_text": full_text,
                    "post_url": post_url,
                    "source_type": source_type,
                    "published_at": published_at,
                }
            )
        return result
