"""Shared CLI helpers for Xerberus scan/backfill scripts."""

import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.xerberus.constants import resolve_xerberus_api_email, resolve_xerberus_database_path
from eth_defi.xerberus.scanner import scan_xerberus
from eth_defi.xerberus.session import create_xerberus_session


def run_xerberus_scan_cli(
    *,
    title: str,
    log_file: Path,
    dry_run: bool | None = None,
    show_top_pools: bool = False,
) -> None:
    """Run a Xerberus registry scan with env-based configuration.

    Credentials come from ``XERBERUS_API_KEY`` and ``XERBERUS_API_EMAIL``
    (operator-supplied only). See ``README-xerberus.md``.

    :param title:
        Human-readable banner for stdout.
    :param log_file:
        Log file path for this invocation.
    :param dry_run:
        Override dry-run. When ``None``, reads ``DRY_RUN`` env var.
    :param show_top_pools:
        When ``True``, print the top 10 scored registry pools after success.
    """
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level, log_file=log_file)

    if dry_run is None:
        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    db_path = resolve_xerberus_database_path()
    fetch_vault_lists = os.environ.get("XERBERUS_FETCH_VAULT_LIST", "true").lower() == "true"
    fetch_reports = os.environ.get("XERBERUS_FETCH_REPORTS", "true").lower() == "true"
    api_key = os.environ.get("XERBERUS_API_KEY")
    api_email = resolve_xerberus_api_email()

    print(title)
    print(f"Database path: {db_path}")
    print(f"DRY_RUN: {dry_run}")
    print(f"Fetch vault list: {fetch_vault_lists}")
    print(f"Fetch reports: {fetch_reports}")
    print(f"XERBERUS_API_EMAIL set: {bool(api_email)}")

    session = create_xerberus_session(api_key=api_key, api_email=api_email)
    db = scan_xerberus(
        session=session,
        db_path=db_path,
        fetch_vault_lists=fetch_vault_lists,
        fetch_reports=fetch_reports,
        dry_run=dry_run,
    )
    if dry_run:
        print("Dry run complete — no database written.")
        return

    assert db is not None
    try:
        counts = db.get_entity_counts()
        print("\nComplete!")
        print(tabulate([counts], headers="keys", tablefmt="fancy_grid"))
        if show_top_pools:
            pools = db.get_latest_registry_entities(entity_type="pool")
            if len(pools) > 0:
                top = pools.dropna(subset=["score"]).sort_values("score", ascending=False).head(10)
                if len(top) > 0:
                    print("\nTop 10 pools by score:")
                    cols = [c for c in ("name", "chain", "address", "score", "entity_id") if c in top.columns]
                    print(tabulate(top[cols].to_dict("records"), headers="keys", tablefmt="fancy_grid"))
    finally:
        db.close()

    print("\nAll ok")
