"""Capped dendrogram report URL backfill for Xerberus pools.

Requires an existing DuckDB with registry pool rows.

Usage:

.. code-block:: shell

    source .local-test.env && \\
      XERBERUS_REPORT_LIMIT=50 \\
      poetry run python scripts/xerberus/backfill-xerberus-reports.py
"""

import logging
import os
from pathlib import Path

from eth_defi.utils import setup_console_logging
from eth_defi.xerberus.constants import (
    XERBERUS_DEFAULT_REPORT_LIMIT,
    resolve_xerberus_api_email,
    resolve_xerberus_database_path,
)
from eth_defi.xerberus.scanner import scan_xerberus
from eth_defi.xerberus.session import create_xerberus_session

logger = logging.getLogger(__name__)


def main() -> None:
    """Backfill report URLs only (registry refresh + capped reports).

    Uses tqdm on the paced report download loop so long runs stay observable.
    """
    # Prefer visible phase logs for operator/agent runs (override with LOG_LEVEL).
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/xerberus-reports.log"),
    )

    db_path = resolve_xerberus_database_path()
    report_limit = int(os.environ.get("XERBERUS_REPORT_LIMIT", str(XERBERUS_DEFAULT_REPORT_LIMIT)))
    api_key = os.environ.get("XERBERUS_API_KEY")
    api_email = resolve_xerberus_api_email()

    print(f"Report backfill limit: {report_limit}")
    print(f"Database: {db_path}")
    print(f"XERBERUS_API_EMAIL set: {bool(api_email)}")
    print("Progress: registry refresh, then report URLs via tqdm (paced ~7.5s per HTTP call).")

    session = create_xerberus_session(api_key=api_key, api_email=api_email)
    db = scan_xerberus(
        session=session,
        db_path=db_path,
        fetch_vault_lists=False,
        fetch_reports=True,
        report_limit=report_limit,
    )
    assert db is not None
    try:
        print(db.get_entity_counts())
    finally:
        db.close()
    print("All ok")


if __name__ == "__main__":
    main()
