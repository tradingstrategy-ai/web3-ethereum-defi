"""Cross-validate DuckDB-computed trader metrics against Hyperliquid API.

Compares our locally computed PnL and volume from the trade history
DuckDB against the Hyperliquid ``portfolio`` and ``clearinghouseState``
API endpoints. Helps verify that the equity curve reconstruction and
daily PnL aggregation are correct.

Usage:

.. code-block:: shell

    # Validate top 5 traders by net PnL
    LOG_LEVEL=info TOP_N=5 poetry run python scripts/hyperliquid/verify-trader-metrics.py

    # Validate specific addresses
    ADDRESSES=0x1234...,0x5678... poetry run python scripts/hyperliquid/verify-trader-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``TOP_N``: Number of top traders to validate (by net PnL). Default: 10
- ``ADDRESSES``: Comma-separated addresses to validate (overrides TOP_N).
- ``TRADE_HISTORY_DB_PATH``: Source DuckDB path. Default: standard location.
- ``CACHE_DB_PATH``: Cache DuckDB path. Default: standard location.
"""

import logging
import os
from pathlib import Path

import duckdb
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.api import (
    fetch_perp_clearinghouse_state,
    fetch_portfolio,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Default source DB path
DEFAULT_SOURCE_DB_PATH = Path("~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb").expanduser()

#: Default cache DB path
DEFAULT_CACHE_DB_PATH = Path("~/.tradingstrategy/vaults/hyperliquid/trader-analysis-cache.duckdb").expanduser()

#: PnL tolerance for PASS/WARN (fraction)
PNL_WARN_THRESHOLD = 0.05

#: PnL tolerance for WARN/FAIL (fraction)
PNL_FAIL_THRESHOLD = 0.50

#: Volume tolerance for PASS/WARN (fraction)
VOLUME_WARN_THRESHOLD = 0.10


def _pct_diff(ours: float, theirs: float) -> float | None:
    """Compute percentage difference: (ours - theirs) / |theirs|."""
    if theirs == 0:
        return None
    return (ours - theirs) / abs(theirs)


def _status(pct_diff: float | None, warn_threshold: float, fail_threshold: float) -> str:
    """Classify as PASS/WARN/FAIL based on percentage difference."""
    if pct_diff is None:
        return "?"
    abs_diff = abs(pct_diff)
    if abs_diff <= warn_threshold:
        return "PASS"
    elif abs_diff <= fail_threshold:
        return "WARN"
    else:
        return "FAIL"


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=default_log_level)

    top_n = int(os.environ.get("TOP_N", "10"))
    addresses_str = os.environ.get("ADDRESSES", "")
    source_db_path = Path(os.environ.get("TRADE_HISTORY_DB_PATH", str(DEFAULT_SOURCE_DB_PATH)))
    cache_db_path = Path(os.environ.get("CACHE_DB_PATH", str(DEFAULT_CACHE_DB_PATH)))

    assert source_db_path.exists(), f"Source DB not found: {source_db_path}"

    source_con = duckdb.connect(str(source_db_path), read_only=True)

    # Determine which addresses to validate
    if addresses_str:
        target_addresses = [a.strip().lower() for a in addresses_str.split(",") if a.strip()]
        logger.info("Validating %d specified addresses", len(target_addresses))
    elif cache_db_path.exists():
        cache_con = duckdb.connect(str(cache_db_path), read_only=True)
        rows = cache_con.execute(
            """
            SELECT address FROM trader_metrics
            ORDER BY net_pnl DESC NULLS LAST
            LIMIT ?
        """,
            [top_n],
        ).fetchall()
        target_addresses = [r[0] for r in rows]
        cache_con.close()
        logger.info("Validating top %d traders by net PnL from cache", len(target_addresses))
    else:
        # Fall back to source DB
        rows = source_con.execute(
            """
            SELECT f.address, SUM(f.closed_pnl) as total_pnl
            FROM fills f
            INNER JOIN accounts a ON f.address = a.address
            WHERE a.is_vault = FALSE
            GROUP BY f.address
            ORDER BY total_pnl DESC
            LIMIT ?
        """,
            [top_n],
        ).fetchall()
        target_addresses = [r[0] for r in rows]
        logger.info("Validating top %d traders by closed PnL from source", len(target_addresses))

    if not target_addresses:
        print("No traders found to validate.")
        source_con.close()
        return

    session = create_hyperliquid_session()

    print("=" * 90)
    print(f"Validating {len(target_addresses)} traders against Hyperliquid API")
    print("=" * 90)

    results = []

    for address in tqdm(target_addresses, desc="Validating traders"):
        # Our metrics from source DB
        our_data = source_con.execute(
            """
            SELECT
                SUM(f.closed_pnl) as total_closed_pnl,
                SUM(f.fee) as total_fees,
                SUM(f.sz * f.px) as total_volume,
                COUNT(*) as fill_count,
                MIN(f.ts) as first_fill_ts,
                MAX(f.ts) as last_fill_ts
            FROM fills f
            WHERE f.address = ?
        """,
            [address],
        ).fetchone()

        our_funding = source_con.execute(
            """
            SELECT COALESCE(SUM(usdc), 0) as total_funding
            FROM funding WHERE address = ?
        """,
            [address],
        ).fetchone()

        label_row = source_con.execute("SELECT label FROM accounts WHERE address = ?", [address]).fetchone()
        label = label_row[0] if label_row and label_row[0] else address[:12]

        our_pnl = (our_data[0] or 0) + (our_funding[0] or 0) - (our_data[1] or 0)
        our_volume = our_data[2] or 0
        our_fill_count = our_data[3] or 0

        # API metrics
        api_pnl = None
        api_volume = None
        api_account_value = None
        api_ntl_pos = None

        try:
            portfolio = fetch_portfolio(session, address)
            if portfolio is not None:
                api_pnl = float(portfolio.all_time_pnl) if portfolio.all_time_pnl is not None else None
                api_volume = float(portfolio.all_time_volume) if portfolio.all_time_volume is not None else None
        except Exception:
            logger.exception("Failed to fetch portfolio for %s", address)

        try:
            state = fetch_perp_clearinghouse_state(session, address)
            if state and state.margin_summary:
                api_account_value = float(state.margin_summary.account_value)
                api_ntl_pos = float(state.margin_summary.total_ntl_pos)
        except Exception:
            logger.exception("Failed to fetch clearinghouse state for %s", address)

        # Compute deltas
        pnl_pct = _pct_diff(our_pnl, api_pnl) if api_pnl is not None else None
        vol_pct = _pct_diff(our_volume, api_volume) if api_volume is not None else None

        # Traders near the 10K fill API cap will have large discrepancies
        # because our DB only covers a fraction of their history.
        # Downgrade FAIL → WARN for fill-capped traders.
        fill_capped = our_fill_count >= 9000

        pnl_status = _status(pnl_pct, PNL_WARN_THRESHOLD, PNL_FAIL_THRESHOLD)
        vol_status = _status(vol_pct, VOLUME_WARN_THRESHOLD, PNL_FAIL_THRESHOLD)

        if fill_capped:
            if pnl_status == "FAIL":
                pnl_status = "WARN*"
            if vol_status == "FAIL":
                vol_status = "WARN*"

        results.append(
            {
                "Label": label,
                "Our PnL": f"${our_pnl:,.0f}",
                "API PnL": f"${api_pnl:,.0f}" if api_pnl is not None else "-",
                "PnL diff": f"{pnl_pct * 100:+.1f}%" if pnl_pct is not None else "-",
                "PnL": pnl_status,
                "Our vol": f"${our_volume:,.0f}",
                "API vol": f"${api_volume:,.0f}" if api_volume is not None else "-",
                "Vol diff": f"{vol_pct * 100:+.1f}%" if vol_pct is not None else "-",
                "Vol": vol_status,
                "Acct val": f"${api_account_value:,.0f}" if api_account_value is not None else "-",
                "Fills": f"{our_fill_count:,}",
            }
        )

    print()
    print(tabulate(results, headers="keys", tablefmt="fancy_grid"))

    # Summary
    pass_count = sum(1 for r in results if r["PnL"] == "PASS")
    warn_count = sum(1 for r in results if r["PnL"] in ("WARN", "WARN*"))
    fail_count = sum(1 for r in results if r["PnL"] == "FAIL")
    unknown_count = sum(1 for r in results if r["PnL"] == "?")

    print(f"\n--- PnL validation summary ---")
    print(f"  PASS: {pass_count}  WARN: {warn_count}  FAIL: {fail_count}  Unknown: {unknown_count}")
    print()
    print("WARN* = expected discrepancy for fill-capped traders (>9K fills in our DB).")
    print("The Hyperliquid API only returns the 10K most recent fills per account, so our")
    print("cumulative PnL will be lower than the API's all-time value for active traders.")

    source_con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
