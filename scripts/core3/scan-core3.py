"""Scan all Core3 projects and store risk data in DuckDB.

This script fetches project risk intelligence from the Core3 API and stores
point-in-time snapshots plus PoL time-series in a DuckDB database for
historical tracking.

Usage:

.. code-block:: shell

    # Basic usage (scans all projects)
    source .local-test.env && poetry run python scripts/core3/scan-core3.py

    # With debug logging
    source .local-test.env && LOG_LEVEL=info poetry run python scripts/core3/scan-core3.py

    # Limited scan for testing
    source .local-test.env && LIMIT=10 poetry run python scripts/core3/scan-core3.py

    # Include section details (security, financial, etc.)
    source .local-test.env && FETCH_SECTIONS=true poetry run python scripts/core3/scan-core3.py

Environment variables:

- ``CORE3_API_KEY``: Core3 API key (required, prefixed ``core3_``)
- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/core3/risk-data.duckdb
- ``LIMIT``: Limit the number of projects to scan (for testing). Default: None (scan all)
- ``MAX_WORKERS``: Maximum number of parallel workers. Default: 8
- ``FETCH_SECTIONS``: Set to ``true`` to also fetch section detail endpoints. Default: false
"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.core3.constants import CORE3_DATABASE_PATH
from eth_defi.core3.scanner import scan_projects
from eth_defi.core3.session import create_core3_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/core3-scan.log"),
    )

    logger.info("Using log level: %s", default_log_level)

    db_path_str = os.environ.get("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str).expanduser()
    else:
        db_path = CORE3_DATABASE_PATH

    limit_str = os.environ.get("LIMIT")
    limit = int(limit_str) if limit_str else None

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    fetch_sections = os.environ.get("FETCH_SECTIONS", "false").lower() == "true"

    print(f"Scanning Core3 projects...")
    print(f"Database path: {db_path}")
    if limit:
        print(f"Limit: {limit} projects")
    print(f"Max workers: {max_workers}")
    print(f"Fetch sections: {fetch_sections}")

    session = create_core3_session()

    db = scan_projects(
        session=session,
        db_path=db_path,
        limit=limit,
        max_workers=max_workers,
        fetch_sections=fetch_sections,
    )

    try:
        project_count = db.get_project_count()
        snapshot_count = db.get_snapshot_count()
        pol_daily_count = db.get_pol_daily_count()

        print(f"\nScan complete!")
        print(f"Total projects: {project_count:,}")
        print(f"Total snapshots: {snapshot_count:,}")
        print(f"PoL daily rows: {pol_daily_count:,}")

        df = db.get_latest_project_snapshots()
        if len(df) > 0:
            print("\nTop 10 projects by rank:")
            top_10 = df.head(10)[["slug", "name", "rank", "pol_score", "pol_rating", "market_cap_usd"]].copy()
            top_10["pol_score"] = top_10["pol_score"].apply(lambda x: f"{x:.2f}" if x is not None else "")

            def _fmt_market_cap(x):
                if x is None or x == "":
                    return ""
                try:
                    return f"${int(x):,}"
                except (ValueError, TypeError):
                    return str(x)

            top_10["market_cap_usd"] = top_10["market_cap_usd"].apply(_fmt_market_cap)
            table_fmt = tabulate(
                top_10.to_dict("records"),
                headers="keys",
                tablefmt="fancy_grid",
            )
            print(table_fmt)
    finally:
        db.close()

    print("\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
