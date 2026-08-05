"""Persistent rate-limit state maintenance.

The scanner keeps SQLite-backed :mod:`pyrate_limiter` buckets beneath
``~/.tradingstrategy``.  These buckets are short-lived request accounting,
not business data.  They are deliberately reset on every
``scan-vaults-all-chains`` process restart, before its first network read.

Persisting the SQLite files remains useful while one scanner process runs,
because parallel workers share their API quota.  Carrying their timestamps
across a restart is neither required nor safe: a changed clock source or a
restarted monotonic clock can make a one-minute throttle appear to last for
years.  This module therefore retains the database schemas but clears every
``ratelimit_*`` request-history table at scanner startup.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

#: Root directory for persistent scanner state and rate-limit databases.
TRADING_STRATEGY_STATE_DIRECTORY = Path("~/.tradingstrategy").expanduser()

#: File name used by every SQLite-backed scanner throttler.
SQLITE_RATE_LIMIT_DATABASE_FILENAME = "rate-limit.sqlite"


def clear_sqlite_rate_limit_databases(state_directory: Path = TRADING_STRATEGY_STATE_DIRECTORY) -> tuple[Path, ...]:
    """Clear request history from all SQLite-backed throttlers.

    The persistent rate-limit databases contain only short-lived request
    timestamps.  They are intentionally reset after every scanner restart;
    clearing their ``ratelimit_*`` tables is safe and leaves the SQLite schema
    in place.  The caller must ensure no concurrent scan is using the
    databases; :mod:`eth_defi.vault.scan_all_chains` does this while holding
    its pipeline lock.

    :param state_directory:
        Root directory below which scanner throttler databases are searched.
        Defaults to the persistent ``~/.tradingstrategy`` directory mounted
        into the production scanner container.

    :return:
        Paths of databases whose rate-limit tables were cleared.

    :raises sqlite3.DatabaseError:
        If a matching database cannot be opened or changed.  Starting a scan
        with potentially invalid throttle state is less safe than failing
        before any network reads.
    """
    if not state_directory.exists():
        return ()

    cleared_databases: list[Path] = []
    for database_path in sorted(state_directory.rglob(SQLITE_RATE_LIMIT_DATABASE_FILENAME)):
        with sqlite3.connect(database_path, timeout=60) as connection:
            table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'ratelimit_*'").fetchall()
            for (table_name,) in table_rows:
                quoted_table_name = table_name.replace('"', '""')
                connection.execute(f'DELETE FROM "{quoted_table_name}"')  # noqa: S608 -- SQLite identifiers cannot be bound parameters.

        cleared_databases.append(database_path)
        logger.info("Cleared SQLite rate-limit state: %s", database_path)

    return tuple(cleared_databases)
