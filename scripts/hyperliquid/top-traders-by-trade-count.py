"""Fetch top Hyperliquid traders by trade count from ASXN Hyperscreener.

Reverse-engineered from ``hyperscreener.asxn.xyz/traders/overview``.
Fetches the top 1000 traders by trade count from a public CloudFront
endpoint, enriches with PnL/volume from the Hyperliquid leaderboard,
and fetches live margin data via ``clearinghouseState``.

Outputs a JSON file and prints two summary tables:
1. Top traders by trade count
2. Top traders by all-time PnL

Data sources (all public, no auth):

- ``d2v1fiwobg9w6.cloudfront.net/largest_user_trade_count`` — top 1000 by trade count
- ``stats-data.hyperliquid.xyz/Mainnet/leaderboard`` — 32K+ traders with PnL/volume
- ``api.hyperliquid.xyz/info`` clearinghouseState — live margin per address
- ``api.hyperliquid.xyz/info`` portfolio — all-time PnL/volume for any address (fallback for non-leaderboard)

Usage:

.. code-block:: shell

    # Default: top 100
    poetry run python scripts/hyperliquid/top-traders-by-trade-count.py

    # Quick test: top 10
    TOP_N=10 poetry run python scripts/hyperliquid/top-traders-by-trade-count.py

    # High trade count filter
    MIN_TRADES=100000000 TOP_N=20 poetry run python scripts/hyperliquid/top-traders-by-trade-count.py

Environment variables:

- ``TOP_N``: Number of top traders to output. Default: 100
- ``MIN_TRADES``: Minimum trade count filter. Default: 0
- ``OUTPUT``: Output JSON path. Default: ~/.tradingstrategy/vaults/hyperliquid/top-traders-by-trade-count.json
- ``MAX_WORKERS``: Parallel threads for clearinghouseState. Default: 4
- ``LOG_LEVEL``: Logging level. Default: warning
"""

import json
import logging
import os
from pathlib import Path
from typing import TypedDict

import requests
from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.api import (
    PerpClearinghouseState,
    PortfolioAllTimeData,
    fetch_leaderboard,
    fetch_perp_clearinghouse_state,
    fetch_portfolio,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: ASXN Hyperscreener CloudFront endpoint for top traders by trade count
TRADE_COUNT_URL = "https://d2v1fiwobg9w6.cloudfront.net/largest_user_trade_count"

#: Default output path for top traders JSON
DEFAULT_OUTPUT_PATH = Path("~/.tradingstrategy/vaults/hyperliquid/top-traders-by-trade-count.json").expanduser()


class TraderRecord(TypedDict):
    """Schema for a single trader record in the output JSON."""

    rank: int
    address: str
    display_name: str | None
    trade_count: int
    # From leaderboard (allTime window)
    all_time_pnl: float | None
    all_time_roi: float | None
    all_time_volume: float | None
    leaderboard_account_value: float | None
    # From clearinghouseState (live)
    live_account_value: float | None
    total_notional_position: float | None
    total_margin_used: float | None
    open_position_count: int | None
    # Computed
    pnl_to_account_value: float | None


def _human(n: float | None) -> str:
    """Format a number with M/k suffixes for display."""
    if n is None:
        return "-"
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${n / 1_000:.0f}k"
    return f"${n:,.0f}"


def _pct(n: float | None) -> str:
    """Format a ratio as percentage."""
    if n is None:
        return "-"
    return f"{n * 100:.1f}%"


def _trade_count_human(n: int) -> str:
    """Format trade count with M/k suffixes."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


class TradeCountEntry(TypedDict):
    """A single entry from the CloudFront trade count endpoint."""

    name: str
    value: float


def fetch_trade_counts() -> list[TradeCountEntry]:
    """Fetch top 1000 traders by trade count from ASXN CloudFront.

    :return:
        List of ``{"name": "0x...", "value": 701547800}`` sorted descending.
    """
    logger.info("Fetching trade counts from %s", TRADE_COUNT_URL)
    resp = requests.get(TRADE_COUNT_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items: list[TradeCountEntry] = data["table_data"]
    logger.info("Got %d traders from trade count endpoint", len(items))
    return items


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    top_n = int(os.environ.get("TOP_N", "100"))
    min_trades = int(os.environ.get("MIN_TRADES", "0"))
    output_path_str = os.environ.get("OUTPUT")
    output_path = Path(output_path_str).expanduser() if output_path_str else DEFAULT_OUTPUT_PATH
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))

    print("Hyperliquid top traders by trade count")  # noqa: T201
    print(f"  Source: ASXN Hyperscreener (CloudFront) + Hyperliquid leaderboard + clearinghouseState")  # noqa: T201
    print(f"  TOP_N={top_n}, MIN_TRADES={min_trades}, MAX_WORKERS={max_workers}")  # noqa: T201

    # Step 1: Fetch bulk data
    trade_counts = fetch_trade_counts()
    leaderboard = fetch_leaderboard()

    # Step 2: Filter by min trades
    if min_trades > 0:
        trade_counts = [t for t in trade_counts if t["value"] >= min_trades]
        print(f"  After MIN_TRADES filter: {len(trade_counts)} traders")  # noqa: T201

    # Step 3: Take top N
    top_traders = trade_counts[:top_n]

    # Find addresses not on the leaderboard — need portfolio API for PnL
    missing_from_leaderboard = {t["name"] for t in top_traders if t["name"].lower() not in leaderboard}
    print(f"\n  Enriching {len(top_traders)} traders ({len(missing_from_leaderboard)} not on leaderboard)...")  # noqa: T201

    # Step 4: Fetch live margin + portfolio data in parallel
    session = create_hyperliquid_session(requests_per_second=2.75)

    def _fetch_one(address: str, needs_portfolio: bool) -> tuple[str, PerpClearinghouseState | None, PortfolioAllTimeData | None]:
        try:
            ch = fetch_perp_clearinghouse_state(session, address)
        except Exception:
            logger.warning("Failed to fetch clearinghouseState for %s", address, exc_info=True)
            ch = None
        pf = fetch_portfolio(session, address) if needs_portfolio else None
        return address, ch, pf

    live_data: dict[str, PerpClearinghouseState] = {}
    portfolio_data: dict[str, PortfolioAllTimeData] = {}
    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(_fetch_one)(t["name"], t["name"] in missing_from_leaderboard) for t in tqdm(top_traders, desc="Fetching live data"))
    for addr, ch, pf in results:
        if ch:
            live_data[addr] = ch
        if pf:
            portfolio_data[addr] = pf

    print(f"  Got clearinghouseState for {len(live_data)}/{len(top_traders)}, portfolio for {len(portfolio_data)}/{len(missing_from_leaderboard)}")  # noqa: T201

    # Step 5: Build records
    records: list[TraderRecord] = []
    for i, t in enumerate(top_traders):
        addr = t["name"].lower()
        lb = leaderboard.get(addr)
        live = live_data.get(t["name"])
        pf = portfolio_data.get(t["name"])

        # Use leaderboard data if available, otherwise fall back to portfolio API
        if lb:
            all_time_pnl = float(lb.all_time_pnl)
            all_time_volume = float(lb.all_time_volume)
            all_time_roi = float(lb.all_time_roi)
            display_name = lb.display_name
            leaderboard_account_value = float(lb.account_value)
        else:
            all_time_pnl = float(pf.all_time_pnl) if pf and pf.all_time_pnl is not None else None
            all_time_volume = float(pf.all_time_volume) if pf and pf.all_time_volume is not None else None
            all_time_roi = None
            display_name = None
            leaderboard_account_value = None

        live_account_value = float(live.margin_summary.account_value) if live else None

        # PnL / account value ratio — how much profit relative to current capital
        # Only meaningful when account has substantial value (> $25k)
        if all_time_pnl is not None and live_account_value and live_account_value > 25_000:
            pnl_to_account_value = all_time_pnl / live_account_value
        else:
            pnl_to_account_value = None

        record: TraderRecord = {
            "rank": i + 1,
            "address": t["name"],
            "display_name": display_name,
            "trade_count": int(t["value"]),
            "all_time_pnl": all_time_pnl,
            "all_time_roi": all_time_roi,
            "all_time_volume": all_time_volume,
            "leaderboard_account_value": leaderboard_account_value,
            "live_account_value": live_account_value,
            "total_notional_position": float(live.margin_summary.total_ntl_pos) if live else None,
            "total_margin_used": float(live.margin_summary.total_margin_used) if live else None,
            "open_position_count": len(live.asset_positions) if live else None,
            "pnl_to_account_value": pnl_to_account_value,
        }
        records.append(record)

    # Step 6: Write JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\n  Wrote {len(records)} records to {output_path}")  # noqa: T201

    # Step 7: Print summary tables

    # Table 1: Top traders by trade count
    print("\n\n=== Top traders by trade count ===\n")  # noqa: T201
    table_headers = ["#", "Name", "Trades", "PnL", "ROI", "PnL/AV", "Volume", "Acct Value", "Margin Used", "Positions"]

    def _table_row(rank: int, r: TraderRecord) -> list:
        return [
            rank,
            r["display_name"] or r["address"][:16] + "...",
            _trade_count_human(r["trade_count"]),
            _human(r["all_time_pnl"]),
            _pct(r["all_time_roi"]),
            _pct(r["pnl_to_account_value"]),
            _human(r["all_time_volume"]),
            _human(r["live_account_value"]),
            _human(r["total_margin_used"]),
            r["open_position_count"] if r["open_position_count"] is not None else "-",
        ]

    rows_by_count = [_table_row(r["rank"], r) for r in records]
    print(
        tabulate(  # noqa: T201
            rows_by_count,
            headers=table_headers,
            tablefmt="simple",
        )
    )

    # Table 2: Top traders by PnL (re-sort)
    print("\n\n=== Top traders by all-time PnL ===\n")  # noqa: T201
    by_pnl = sorted(records, key=lambda r: r["all_time_pnl"] or 0, reverse=True)
    rows_by_pnl = [_table_row(i + 1, r) for i, r in enumerate(by_pnl)]
    print(
        tabulate(  # noqa: T201
            rows_by_pnl,
            headers=table_headers,
            tablefmt="simple",
        )
    )

    # Table 3: Top traders by PnL / account value ratio
    print("\n\n=== Top traders by PnL / account value ===\n")  # noqa: T201
    by_pnl_av = sorted(records, key=lambda r: r["pnl_to_account_value"] or float("-inf"), reverse=True)
    rows_by_pnl_av = [_table_row(i + 1, r) for i, r in enumerate(by_pnl_av)]
    print(
        tabulate(  # noqa: T201
            rows_by_pnl_av,
            headers=table_headers,
            tablefmt="simple",
        )
    )


if __name__ == "__main__":
    main()
