"""Collect and manually verify Neutral Trade data without publishing.

Requires NEUTRAL_TRADE_API_KEY. Initial runs backfill available history;
subsequent runs resume. See README-delta-neutral.md for production rules.
"""

import datetime
import logging
import os
from contextlib import nullcontext
from pathlib import Path

from eth_defi.neutral_trade.metrics import NeutralTradeDatabase, run_scan
from eth_defi.neutral_trade.reporting import render_verification_report
from eth_defi.utils import setup_console_logging
from eth_defi.vault.scanner_lock import acquire_scanner_lifecycle_guard
from eth_defi.vault.vaultdb import get_pipeline_data_dir


def main() -> None:
    """Collect into a private archive and print persisted-data inspection tables.

    Production archive access takes the scanner lifecycle guard. Deliberate
    production refreshes must use a numbered migration, never this command.

    :return: None after reporting; failures exit nonzero.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    assert key, "NEUTRAL_TRADE_API_KEY is required"
    canonical = get_pipeline_data_dir().resolve() / "neutral-trade-vaults.duckdb"
    path = Path(os.environ.get("DB_PATH", str(canonical))).expanduser().resolve()
    mode = os.environ.get("HISTORY_MODE", "incremental")
    assert mode in {"incremental", "refresh"}
    start = datetime.date.fromisoformat(os.environ["START_DATE"]) if os.environ.get("START_DATE") else None
    end = datetime.date.fromisoformat(os.environ["END_DATE"]) if os.environ.get("END_DATE") else None
    assert not (path == canonical and (mode == "refresh" or start or end)), "Production refresh/date overrides require a numbered migration"
    with acquire_scanner_lifecycle_guard(canonical.parent) if path == canonical else nullcontext():
        with NeutralTradeDatabase(path) as db:
            try:
                result = run_scan(db, key, max_workers=int(os.environ.get("MAX_WORKERS", "2")), page_budget=int(os.environ.get("PAGE_BUDGET", "10000")), time_budget=float(os.environ.get("TIME_BUDGET", "3600")), vault_ids={v.strip() for v in os.environ.get("VAULT_IDS", "").split(",") if v.strip()} or None, start_date=start, end_date=end, refresh=mode == "refresh")
            finally:
                report = render_verification_report(db)
                print(report)
                if os.environ.get("REPORT_PATH"):
                    Path(os.environ["REPORT_PATH"]).write_text(report, encoding="utf-8")
            if result.failures:
                message = "Collection has failed operations; inspect the report"
                raise SystemExit(message)
            if result.pending:
                logging.getLogger(__name__).warning("History is incomplete; rerun to resume")


if __name__ == "__main__":
    main()
