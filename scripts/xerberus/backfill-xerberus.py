"""Operator backfill / bootstrap for Xerberus DuckDB.

Because the public API has no historical series, "backfill" means a full
current snapshot refresh into the target database path.

Requires ``XERBERUS_API_KEY`` and ``XERBERUS_API_EMAIL`` (registered email for
the key). Agents must not invent the email; only use operator-supplied values.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/xerberus/backfill-xerberus.py

    source .local-test.env && DRY_RUN=true \\
      poetry run python scripts/xerberus/backfill-xerberus.py

    source .local-test.env && \\
      XERBERUS_DATABASE_PATH=/tmp/xerberus-backfill.duckdb \\
      poetry run python scripts/xerberus/backfill-xerberus.py
"""

import os
from pathlib import Path

from eth_defi.xerberus.cli import run_xerberus_scan_cli


def main() -> None:
    """Run Xerberus backfill (full current snapshot) or dry-run plan.

    Long paced phases (vault lists, report URL backfill) use tqdm progress
    bars via :func:`~eth_defi.xerberus.scanner.scan_xerberus`. Default log
    level is ``info`` so phase boundaries stay visible without silent multi-minute runs.
    """
    # Prefer visible phase logs for operator/agent runs (override with LOG_LEVEL).
    os.environ.setdefault("LOG_LEVEL", "info")
    print("Note: public API has no history; this refreshes current scores only.")
    print("Progress: registry → vault lists (tqdm) → report URLs (tqdm, paced ~7.5s/HTTP).")
    run_xerberus_scan_cli(
        title="Xerberus backfill",
        log_file=Path("logs/xerberus-backfill.log"),
        dry_run=None,  # honour DRY_RUN env
        show_top_pools=False,
    )


if __name__ == "__main__":
    main()
