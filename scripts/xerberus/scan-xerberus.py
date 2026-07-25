"""Scan Xerberus registry and vault lists into DuckDB.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/xerberus/scan-xerberus.py

    source .local-test.env && LOG_LEVEL=info poetry run python scripts/xerberus/scan-xerberus.py

    source .local-test.env && XERBERUS_FETCH_VAULT_LIST=false \\
      poetry run python scripts/xerberus/scan-xerberus.py

Environment variables:

- ``XERBERUS_API_KEY``: API key (required)
- ``XERBERUS_API_EMAIL``: Email registered with the key (required).
  **Do not invent this value** — only use the operator-supplied registered
  address.
- ``XERBERUS_DATABASE_PATH``: DuckDB path (default under ~/.tradingstrategy/vaults/xerberus/)
- ``XERBERUS_FETCH_VAULT_LIST``: ``true``/``false`` (default true)
- ``XERBERUS_FETCH_REPORTS``: ``true``/``false`` (default true)
- ``LOG_LEVEL``: default warning
"""

from pathlib import Path

from eth_defi.xerberus.cli import run_xerberus_scan_cli


def main() -> None:
    """Run a full Xerberus scan and print summary counts."""
    run_xerberus_scan_cli(
        title="Scanning Xerberus registry…",
        log_file=Path("logs/xerberus-scan.log"),
        dry_run=False,
        show_top_pools=True,
    )


if __name__ == "__main__":
    main()
