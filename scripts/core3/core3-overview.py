"""Display an overview of the Core3 DuckDB database contents.

Shows one row per project with key metrics, data counts, and last
updated timestamps. Useful for verifying scan results.

Usage:

.. code-block:: shell

    poetry run python scripts/core3/core3-overview.py

    # Custom database path
    DB_PATH=~/my-core3.duckdb poetry run python scripts/core3/core3-overview.py

Environment variables:

- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/core3/risk-data.duckdb
"""

import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.core3.constants import CORE3_DATABASE_PATH
from eth_defi.core3.database import Core3Database


def _fmt_market_cap(x) -> str:
    if x is None or x == "":
        return ""
    try:
        return f"${int(x):,}"
    except (ValueError, TypeError):
        return str(x)


def main():
    db_path_str = os.environ.get("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str).expanduser()
    else:
        db_path = CORE3_DATABASE_PATH

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Run scan-core3.py first to populate the database.")
        return

    db = Core3Database(db_path)

    try:
        # Summary counts
        project_count = db.get_project_count()
        snapshot_count = db.get_snapshot_count()
        pol_daily_count = db.get_pol_daily_count()

        print(f"Database: {db_path}")
        print(f"Projects: {project_count:,}")
        print(f"Snapshots: {snapshot_count:,}")
        print(f"PoL daily rows: {pol_daily_count:,}")

        # Per-project overview: latest snapshot + data counts + timestamps
        with db._db_lock:
            df = db.con.execute("""
                SELECT
                    ps.slug,
                    ps.name,
                    ps.rank,
                    ps.pol_score,
                    ps.pol_rating,
                    ps.market_cap_usd,
                    ps.fetched_at AS last_snapshot,
                    snap_counts.snapshot_count,
                    COALESCE(pol_counts.pol_points, 0) AS pol_points,
                    pol_counts.pol_first,
                    pol_counts.pol_last,
                    COALESCE(cat_counts.cat_points, 0) AS cat_points
                FROM project_snapshots ps
                INNER JOIN (
                    SELECT slug, MAX(fetched_at) AS max_fetched_at
                    FROM project_snapshots
                    GROUP BY slug
                ) latest ON ps.slug = latest.slug AND ps.fetched_at = latest.max_fetched_at
                LEFT JOIN (
                    SELECT slug, COUNT(*) AS snapshot_count
                    FROM project_snapshots
                    GROUP BY slug
                ) snap_counts ON ps.slug = snap_counts.slug
                LEFT JOIN (
                    SELECT slug, COUNT(*) AS pol_points, MIN(ts) AS pol_first, MAX(ts) AS pol_last
                    FROM pol_daily
                    GROUP BY slug
                ) pol_counts ON ps.slug = pol_counts.slug
                LEFT JOIN (
                    SELECT slug, COUNT(*) AS cat_points
                    FROM pol_category_daily
                    GROUP BY slug
                ) cat_counts ON ps.slug = cat_counts.slug
                ORDER BY ps.rank NULLS LAST
            """).df()

        if len(df) == 0:
            print("\nNo projects found.")
            return

        # Format columns for display
        display = df[
            [
                "slug",
                "name",
                "rank",
                "pol_score",
                "pol_rating",
                "market_cap_usd",
                "snapshot_count",
                "pol_points",
                "cat_points",
                "pol_first",
                "pol_last",
                "last_snapshot",
            ]
        ].copy()

        display["pol_score"] = display["pol_score"].apply(lambda x: f"{x:.2f}" if x is not None else "")
        display["market_cap_usd"] = display["market_cap_usd"].apply(_fmt_market_cap)
        display["pol_first"] = display["pol_first"].apply(lambda x: x.strftime("%Y-%m-%d") if x is not None else "")
        display["pol_last"] = display["pol_last"].apply(lambda x: x.strftime("%Y-%m-%d") if x is not None else "")
        display["last_snapshot"] = display["last_snapshot"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M") if x is not None else "")

        display.columns = [
            "slug",
            "name",
            "rank",
            "pol",
            "rating",
            "market_cap",
            "snaps",
            "pol_pts",
            "cat_pts",
            "pol_from",
            "pol_to",
            "last_updated",
        ]

        table = tabulate(
            display.to_dict("records"),
            headers="keys",
            tablefmt="fancy_grid",
        )
        print(f"\n{table}")

        # Index PoL summary
        with db._db_lock:
            index_row = db.con.execute("""
                SELECT COUNT(*), MIN(ts), MAX(ts)
                FROM pol_daily
                WHERE slug = '__index__'
            """).fetchone()

        if index_row[0] > 0:
            print(f"\nIndex PoL: {index_row[0]:,} points from {index_row[1]:%Y-%m-%d} to {index_row[2]:%Y-%m-%d}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
