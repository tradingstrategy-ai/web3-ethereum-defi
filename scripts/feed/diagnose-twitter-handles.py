"""Diagnostics: show first post, last post, and post count for all Twitter handles.

Reads directly from the DuckDB database — no X API calls needed.

Usage::

    poetry run python scripts/feed/diagnose-twitter-handles.py

Docker::

    docker compose run --rm -T --no-deps --entrypoint python post-scanner scripts/feed/diagnose-twitter-handles.py

Optional environment variables:

- ``DB_PATH``: DuckDB path, default
  ``~/.tradingstrategy/vaults/vault-post-database.duckdb``
"""

import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase


def _get_db_path() -> Path:
    """Read the configured feed database path.

    :return:
        DuckDB database path.
    """

    db_path = os.environ.get("DB_PATH")
    return Path(db_path).expanduser() if db_path else DEFAULT_VAULT_POST_DATABASE


def main() -> None:
    """Print a diagnostics table of Twitter handles with post statistics."""

    db_path = _get_db_path()

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        raise SystemExit(1)

    with VaultPostDatabase(db_path) as db:
        rows = db.con.execute(
            """
            SELECT
                ts.feeder_id,
                ts.source_key                                           AS handle,
                ts.name,
                COUNT(p.external_post_id)                              AS post_count,
                MIN(COALESCE(p.published_at, p.fetched_at))            AS first_post,
                MAX(COALESCE(p.published_at, p.fetched_at))            AS last_post,
                ts.last_post_published_at
            FROM tracked_sources ts
            LEFT JOIN posts p ON p.source_id = ts.source_id
            WHERE ts.source_type = 'twitter'
            GROUP BY
                ts.feeder_id,
                ts.source_key,
                ts.name,
                ts.last_post_published_at
            ORDER BY ts.feeder_id, ts.source_key
            """
        ).fetchall()

    if not rows:
        print("No Twitter sources found in the database.")
        return

    table = []
    for feeder_id, handle, name, post_count, first_post, last_post, stored_last in rows:
        first_str = first_post.isoformat(sep=" ", timespec="seconds") if first_post else "-"
        last_str = last_post.isoformat(sep=" ", timespec="seconds") if last_post else "-"
        stored_str = stored_last.isoformat(sep=" ", timespec="seconds") if stored_last else "NULL"
        table.append([feeder_id, f"@{handle}", name, post_count, first_str, last_str, stored_str])

    print()
    print(
        tabulate(
            table,
            headers=["Feeder", "Handle", "Name", "Posts", "First post", "Last post", "Stored last"],
            tablefmt="fancy_grid",
        )
    )
    print(f"\nTotal: {len(table)} Twitter handles, {sum(r[3] for r in table)} posts.")


if __name__ == "__main__":
    main()
