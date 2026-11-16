"""Manually backfill Neutral Trade vaults and report lifetime performance.

Required: NEUTRAL_TRADE_API_KEY. Optional OUTPUT_DIR defaults to the isolated
logs/neutral-trade-backfill workspace. Production state is never a destination.
This is a manual analysis command; production repairs use numbered migrations.
"""

import os
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.neutral_trade.api import NeutralTradeClient
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase, run_scan
from eth_defi.neutral_trade.performance import build_performance_summary, format_performance_summary
from eth_defi.neutral_trade.reporting import render_verification_report
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir


def main() -> None:
    """Backfill into isolated files, then print and save inspectable data tables.

    Initial runs collect all available approved history; subsequent invocations
    resume persisted cursors. WSOL and private records retain existing admission
    rules. Partial collection is reported and exits nonzero, never as success.

    :return: None after a complete run; raises SystemExit for incomplete work.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    assert key, "NEUTRAL_TRADE_API_KEY is required"
    output = Path(os.environ.get("OUTPUT_DIR", "logs/neutral-trade-backfill")).expanduser().resolve()
    production = get_pipeline_data_dir().resolve()
    assert not output.is_relative_to(production), "Use an isolated analysis directory, not production state"
    output.mkdir(parents=True, exist_ok=True)
    source_path = (output / "source.duckdb").resolve()
    assert not source_path.is_relative_to(production), "Source archive cannot point into production state"
    with NeutralTradeDatabase(source_path) as db:
        result = run_scan(db, key, max_workers=int(os.environ.get("MAX_WORKERS", "2")), page_budget=int(os.environ.get("PAGE_BUDGET", "10000")), time_budget=float(os.environ.get("TIME_BUDGET", "3600")))
        with NeutralTradeClient(key) as client:
            names = {row["bundleKey"]: row["name"] for row in client.fetch_public_yields()["data"]["vaults"]}
        now = native_datetime_utc_now()
        frame = build_performance_summary(db, as_of=now, public_names=names)
        table = format_performance_summary(frame)
        frame.to_csv(output / "performance.csv", index=False)
        (output / "performance.md").write_text(f"Report UTC: {now.isoformat()}\n\n" + table, encoding="utf-8")
        (output / "verification.txt").write_text(render_verification_report(db), encoding="utf-8")
        print(table)
    if result.failures or result.pending:
        message = "Backfill is incomplete; inspect the report and rerun to resume"
        raise SystemExit(message)


if __name__ == "__main__":
    main()
