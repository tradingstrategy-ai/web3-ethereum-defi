"""Tests for vault-post DuckDB schema management and migration."""

import datetime
import json
from pathlib import Path

import duckdb
import pytest

from eth_defi.feed.database import VAULT_POST_COMPRESSION_QUERIES, VaultPostDatabase
from eth_defi.feed.sources import TrackedPostSource


@pytest.mark.parametrize("legacy_has_raw_payload", (False, True), ids=("without-raw-payload", "with-raw-payload"))
def test_migrate_legacy_database_to_latest_storage_with_zstd_post_content(tmp_path: Path, legacy_has_raw_payload: bool) -> None:
    """Migrate legacy vault posts without loss and allocate new source IDs safely.

    1. Create a legacy-format database with source, post, and sync-state rows.
    2. Open it through :class:`VaultPostDatabase` to migrate and validate it.
    3. Reopen it, insert a new source, and verify the ID sequence follows copied rows.
    4. Verify the post body and, when present, raw API payload use native
       Zstandard compression.

    :param tmp_path:
        Pytest-provided directory for the legacy and migrated DuckDB file.
    :param legacy_has_raw_payload:
        Whether the source database has the nullable column added by the
        historical schema upgrade.
    """
    database_path = tmp_path / "legacy-vault-posts.duckdb"
    timestamp = datetime.datetime(2026, 7, 24, 12, 0, 0)  # noqa: DTZ001 - repository datetimes are naive UTC
    expected_new_source_id = 3
    full_text = "Full note tweet body. " * 1_000
    raw_payload = json.dumps({"data": "Raw API payload. " * 1_000})

    legacy = duckdb.connect(str(database_path))
    try:
        legacy.execute("CREATE SEQUENCE tracked_sources_id_seq START 1")
        legacy.execute("""
            CREATE TABLE tracked_sources (
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
        legacy.execute("""
            CREATE TABLE posts (
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
        if legacy_has_raw_payload:
            legacy.execute("ALTER TABLE posts ADD COLUMN raw_payload VARCHAR")
        legacy.execute("""
            CREATE TABLE feed_sync_state (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        legacy.execute(
            """
            INSERT INTO tracked_sources (
                source_id, feeder_id, name, role, website, source_type,
                source_key, canonical_url, added_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [2, "existing", "Existing source", "protocol", "https://existing.example", "rss", "existing", "https://existing.example/feed", timestamp, timestamp],
        )
        legacy.execute(
            """
            INSERT INTO posts (
                source_id, external_post_id, title, post_url, published_at,
                fetched_at, short_description, full_text, ai_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [2, "post-1", "Post", "https://existing.example/post-1", timestamp, timestamp, "Short description", full_text, None],
        )
        if legacy_has_raw_payload:
            legacy.execute("UPDATE posts SET raw_payload = ?", [raw_payload])
        legacy.execute("INSERT INTO feed_sync_state VALUES (?, ?, ?)", ["cursor", "abc", timestamp])
        legacy.execute("CHECKPOINT")
    finally:
        legacy.close()

    database = VaultPostDatabase(database_path)
    try:
        assert len(database.get_tracked_sources_df()) == 1
        assert len(database.get_posts_df()) == 1
        assert database.get_sync_state("cursor") == "abc"
        assert database.con.execute("SELECT raw_payload FROM posts").fetchone()[0] == (raw_payload if legacy_has_raw_payload else None)
    finally:
        database.close()

    database = VaultPostDatabase(database_path)
    try:
        new_source_id = database.upsert_tracked_source(
            TrackedPostSource(
                feeder_id="new",
                name="New source",
                role="curator",
                website="https://new.example",
                source_type="rss",
                source_key="new",
                canonical_url="https://new.example/feed",
                mapping_file=Path("new.yaml"),
            )
        )
        assert new_source_id == expected_new_source_id
    finally:
        database.close()

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        storage_version = connection.execute("SELECT tags['storage_version'] FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
        assert VaultPostDatabase._uses_latest_storage_format(storage_version)
        for column_name, query in VAULT_POST_COMPRESSION_QUERIES.items():
            if column_name == "raw_payload" and not legacy_has_raw_payload:
                continue
            assert {row[0] for row in connection.execute(query).fetchall()} == {"ZSTD"}
    finally:
        connection.close()


def test_migration_refuses_unrecognised_source_columns(tmp_path: Path) -> None:
    """Retain the original database when its schema cannot be copied losslessly.

    :param tmp_path:
        Pytest-provided directory for the legacy database file.
    """
    database_path = tmp_path / "unknown-column-vault-posts.duckdb"
    legacy = duckdb.connect(str(database_path))
    try:
        legacy.execute("""
            CREATE TABLE posts (
                source_id BIGINT NOT NULL,
                external_post_id VARCHAR NOT NULL,
                title VARCHAR,
                post_url VARCHAR,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP NOT NULL,
                short_description VARCHAR NOT NULL,
                full_text VARCHAR NOT NULL,
                ai_summary VARCHAR,
                raw_payload VARCHAR,
                source_api_version VARCHAR
            )
        """)
        legacy.execute("CHECKPOINT")
    finally:
        legacy.close()

    with pytest.raises(RuntimeError, match="unrecognised columns"):
        VaultPostDatabase(database_path)

    legacy = duckdb.connect(str(database_path), read_only=True)
    try:
        columns = {row[1] for row in legacy.execute("PRAGMA table_info('posts')").fetchall()}
        assert "source_api_version" in columns
    finally:
        legacy.close()
