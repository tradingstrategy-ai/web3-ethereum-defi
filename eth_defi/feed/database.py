"""DuckDB persistence for vault post tracking."""

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Self

import duckdb
import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.sources import TrackedPostSource


logger = logging.getLogger(__name__)


#: Default DuckDB path for collected vault posts.
DEFAULT_VAULT_POST_DATABASE = Path("~/.tradingstrategy/vaults/vault-post-database.duckdb").expanduser()


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
    short_description: str
    #: Best available full text extracted from the feed entry.
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
        self.con = duckdb.connect(str(path))
        self._init_schema()

    def __enter__(self) -> Self:
        """Enter a context-managed database session."""

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the database at the end of a context-managed session."""

        self.close()

    def _init_schema(self) -> None:
        """Create database schema if needed."""

        self.con.execute("CREATE SEQUENCE IF NOT EXISTS tracked_sources_id_seq START 1")

        self.con.execute("""
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
        self.con.execute("ALTER TABLE tracked_sources ADD COLUMN IF NOT EXISTS website VARCHAR")

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                source_id BIGINT NOT NULL,
                external_post_id VARCHAR NOT NULL,
                title VARCHAR,
                post_url VARCHAR,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP NOT NULL,
                short_description VARCHAR NOT NULL,
                full_text VARCHAR NOT NULL,
                ai_summary VARCHAR,
                PRIMARY KEY (source_id, external_post_id)
            )
        """)
        self.con.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS raw_payload VARCHAR")

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS feed_sync_state (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)

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

    def get_tracked_sources_df(self) -> pd.DataFrame:
        """Return tracked source rows for diagnostics."""

        return self.con.execute("SELECT * FROM tracked_sources ORDER BY source_id").df()

    def get_posts_df(self) -> pd.DataFrame:
        """Return stored posts for diagnostics."""

        return self.con.execute("SELECT * FROM posts ORDER BY source_id, COALESCE(published_at, fetched_at)").df()
