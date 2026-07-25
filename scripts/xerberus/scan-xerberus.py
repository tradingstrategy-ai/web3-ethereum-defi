"""Scan / backfill Xerberus registry, vault lists and report URLs into DuckDB.

One operator entrypoint for all Xerberus HTTP refresh work. Behaviour is
controlled by environment variables (no CLI flags).

Because the public API has no historical series, a "backfill" is a full
current snapshot refresh (scores + optional vault lists + optional report URLs).

Requires ``XERBERUS_API_KEY`` and ``XERBERUS_API_EMAIL`` (registered email for
the key). Agents must not invent the email; only use operator-supplied values.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/xerberus/scan-xerberus.py

    source .local-test.env && LOG_LEVEL=info poetry run python scripts/xerberus/scan-xerberus.py

    source .local-test.env && DRY_RUN=true \\
      poetry run python scripts/xerberus/scan-xerberus.py

    # Scores only (skip paced vault lists and report downloads)
    source .local-test.env && XERBERUS_FETCH_VAULT_LIST=false XERBERUS_FETCH_REPORTS=false \\
      poetry run python scripts/xerberus/scan-xerberus.py

    # Registry + report URLs only (skip vault lists)
    source .local-test.env && XERBERUS_FETCH_VAULT_LIST=false XERBERUS_REPORT_LIMIT=500 \\
      poetry run python scripts/xerberus/scan-xerberus.py

    source .local-test.env && \\
      XERBERUS_DATABASE_PATH=/tmp/xerberus-backfill.duckdb \\
      poetry run python scripts/xerberus/scan-xerberus.py

Environment variables:

- ``XERBERUS_API_KEY``: API key (required for live scan)
- ``XERBERUS_API_EMAIL``: Email registered with the key (required).
  **Do not invent this value** — only use the operator-supplied registered
  address.
- ``XERBERUS_DATABASE_PATH``: DuckDB path (default under ~/.tradingstrategy/vaults/xerberus/)
- ``XERBERUS_FETCH_VAULT_LIST``: ``true``/``false`` (default true)
- ``XERBERUS_FETCH_REPORTS``: ``true``/``false`` (default true)
- ``XERBERUS_REPORT_LIMIT``: max report URL fetches this run (default 50)
- ``DRY_RUN``: ``true``/``false`` (default false)
- ``LOG_LEVEL``: default info
"""

import os
from pathlib import Path

from eth_defi.xerberus.cli import run_xerberus_scan_cli


def main() -> None:
    """Run a full or partial Xerberus scan and print summary counts.

    Long paced phases (vault lists, report URL backfill) use tqdm progress
    bars via :func:`~eth_defi.xerberus.scanner.scan_xerberus`.
    """
    # Prefer visible phase logs for operator/agent runs (override with LOG_LEVEL).
    os.environ.setdefault("LOG_LEVEL", "info")
    print("Note: public API has no history; this refreshes current scores only.")
    print("Progress: registry → vault lists (tqdm) → report URLs (tqdm, paced ~7.5s/HTTP).")
    print("Toggle phases with XERBERUS_FETCH_VAULT_LIST / XERBERUS_FETCH_REPORTS / XERBERUS_REPORT_LIMIT.")
    run_xerberus_scan_cli(
        title="Scanning Xerberus registry…",
        log_file=Path("logs/xerberus-scan.log"),
        dry_run=None,  # honour DRY_RUN env
        show_top_pools=True,
    )


if __name__ == "__main__":
    main()
