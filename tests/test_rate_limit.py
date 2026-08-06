"""Tests for persistent rate-limit state maintenance."""

import sqlite3

from eth_defi.rate_limit import clear_sqlite_rate_limit_databases


def test_clear_sqlite_rate_limit_databases(tmp_path):
    """Clear all scanner rate-limit tables without touching other SQLite data.

    Creates rate-limit databases in multiple scanner subdirectories, inserts
    stale request entries, and verifies that the scanner startup maintenance
    removes only the throttle entries.
    """
    hypersync_database = tmp_path / "hypersync" / "rate-limit.sqlite"
    core3_database = tmp_path / "vaults" / "core3" / "rate-limit.sqlite"
    other_database = tmp_path / "vaults" / "business-data.sqlite"

    for database_path in (hypersync_database, core3_database, other_database):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE records (value INTEGER)")
            connection.execute("INSERT INTO records VALUES (1)")

    for database_path in (hypersync_database, core3_database):
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE ratelimit_test (value REAL)")
            connection.execute("INSERT INTO ratelimit_test VALUES (123.0)")

    assert clear_sqlite_rate_limit_databases(tmp_path) == (hypersync_database, core3_database)

    for database_path in (hypersync_database, core3_database):
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM ratelimit_test").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1

    with sqlite3.connect(other_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
