"""Reconstruct and display trade history for a Hyperliquid account.

Fetches fills, funding, and clearinghouse state, then displays:

1. Account overview (value, margin, withdrawable)
2. Current open positions
3. Open trades with funding costs
4. Closed trades with PnL breakdown
5. Summary totals
6. Event-accurate share price history (vaults only)

Usage:

.. code-block:: shell

    # Show trade history for a vault
    ADDRESS=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
      poetry run python scripts/hyperliquid/vault-trade-history.py

    # With custom time range and logging
    ADDRESS=0x1e37a337ed460039d1b15bd3bc489de789768d5e DAYS=60 LOG_LEVEL=info \
      poetry run python scripts/hyperliquid/vault-trade-history.py

Environment variables:

- ``ADDRESS``: Account address (required).
- ``DAYS``: Number of days of history. Default: 30.
- ``LOG_LEVEL``: Logging level. Default: warning.
- ``TRADE_HISTORY_DB_PATH``: DuckDB path for persistent storage (optional).
  When set, data is stored and read from DuckDB.
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.deposit import fetch_vault_deposits
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.trade_history import (
    compute_event_share_prices,
    create_share_price_dataframe,
    create_trade_summary_dataframe,
    fetch_account_trade_history,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    address = os.environ.get("ADDRESS")
    if not address:
        print("ERROR: ADDRESS environment variable is required")
        return

    address = address.strip().lower()
    days = int(os.environ.get("DAYS", "30"))

    db_path_str = os.environ.get("TRADE_HISTORY_DB_PATH")

    print(f"Hyperliquid trade history")
    print(f"Address: {address}")
    print(f"Days: {days}")

    session = create_hyperliquid_session(requests_per_second=2.75)

    end_time = native_datetime_utc_now()
    start_time = end_time - datetime.timedelta(days=days)

    # Optionally sync to DuckDB first
    if db_path_str:
        from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase

        db_path = Path(db_path_str).expanduser()
        print(f"DuckDB path: {db_path}")
        db = HyperliquidTradeHistoryDatabase(db_path)
        try:
            db.add_account(address, is_vault=True)
            db.sync_account(session, address)
            db.save()
        finally:
            db.close()

    # Fetch trade history
    print(f"\nFetching trade history...")
    history = fetch_account_trade_history(
        session,
        address,
        start_time=start_time,
        end_time=end_time,
    )

    # 1. Account overview
    ms = history.margin_summary
    print(f"\n{'=' * 60}")
    print(f"Account overview")
    print(f"{'=' * 60}")
    print(
        tabulate(
            [
                ["Account value", f"${float(ms.account_value):,.2f}"],
                ["Total margin used", f"${float(ms.total_margin_used):,.2f}"],
                ["Total raw USD", f"${float(ms.total_raw_usd):,.2f}"],
                ["Total fills", len(history.fills)],
                ["Fills truncated", history.fills_truncated],
                ["Funding payments", len(history.funding_payments)],
                ["Open trades", len(history.open_trades)],
                ["Closed trades", len(history.closed_trades)],
            ],
            tablefmt="simple",
        )
    )

    # 2. Current open positions
    if history.open_positions:
        print(f"\n{'=' * 60}")
        print(f"Open positions")
        print(f"{'=' * 60}")
        pos_rows = []
        for pos in history.open_positions:
            pos_rows.append(
                [
                    pos.coin,
                    f"{float(pos.size):,.4f}",
                    f"${float(pos.entry_price):,.2f}",
                    f"${float(pos.position_value):,.2f}",
                    f"${float(pos.unrealised_pnl):,.2f}",
                    f"${float(pos.liquidation_price):,.2f}" if pos.liquidation_price else "N/A",
                ]
            )
        print(
            tabulate(
                pos_rows,
                headers=["Coin", "Size", "Entry", "Value", "Unrealised PnL", "Liq price"],
                tablefmt="simple",
            )
        )

    # 3. Open trades
    if history.open_trades:
        print(f"\n{'=' * 60}")
        print(f"Open trades")
        print(f"{'=' * 60}")
        df = create_trade_summary_dataframe(history.open_trades)
        print(
            tabulate(
                df[["coin", "direction", "is_complete", "opened_at", "entry_price", "max_size", "current_size", "realised_pnl", "funding_pnl", "net_pnl", "unrealised_pnl", "fill_count"]].values.tolist(),
                headers=["Coin", "Dir", "Complete", "Opened", "Entry", "Max size", "Cur size", "Realised", "Funding", "Net PnL", "Unrealised", "Fills"],
                tablefmt="simple",
                floatfmt=".2f",
            )
        )

    # 4. Closed trades
    if history.closed_trades:
        print(f"\n{'=' * 60}")
        print(f"Closed trades")
        print(f"{'=' * 60}")
        df = create_trade_summary_dataframe(history.closed_trades)
        print(
            tabulate(
                df[["coin", "direction", "is_complete", "opened_at", "closed_at", "duration", "entry_price", "exit_price", "max_size", "realised_pnl", "funding_pnl", "total_fees", "net_pnl", "fill_count"]].values.tolist(),
                headers=["Coin", "Dir", "Complete", "Opened", "Closed", "Duration", "Entry", "Exit", "Max size", "Realised", "Funding", "Fees", "Net PnL", "Fills"],
                tablefmt="simple",
                floatfmt=".2f",
            )
        )

    # 5. Summary totals
    all_trades = history.closed_trades + history.open_trades
    if all_trades:
        total_realised = sum(float(t.realised_pnl) for t in all_trades)
        total_funding = sum(float(t.funding_pnl) for t in all_trades)
        total_fees = sum(float(t.total_fees) for t in all_trades)
        total_net = sum(float(t.net_pnl) for t in all_trades)
        total_unrealised = sum(float(t.unrealised_pnl) for t in history.open_trades if t.unrealised_pnl is not None)

        print(f"\n{'=' * 60}")
        print(f"Summary")
        print(f"{'=' * 60}")
        print(
            tabulate(
                [
                    ["Total realised PnL", f"${total_realised:,.2f}"],
                    ["Total funding PnL", f"${total_funding:,.2f}"],
                    ["Total fees", f"${total_fees:,.2f}"],
                    ["Total net PnL", f"${total_net:,.2f}"],
                    ["Total unrealised PnL", f"${total_unrealised:,.2f}"],
                ],
                tablefmt="simple",
            )
        )

    # 6. Event-accurate share prices (vault only)
    print(f"\n{'=' * 60}")
    print(f"Event-accurate share prices")
    print(f"{'=' * 60}")

    print("Fetching deposit/withdrawal history...")
    ledger_events = list(
        fetch_vault_deposits(
            session,
            address,
            start_time=start_time,
            end_time=end_time,
        )
    )
    print(f"Ledger events: {len(ledger_events)}")

    sp_events = compute_event_share_prices(
        fills=history.fills,
        funding_payments=history.funding_payments,
        ledger_events=ledger_events,
    )

    if sp_events:
        sp_df = create_share_price_dataframe(sp_events)
        # Show summary stats
        print(
            tabulate(
                [
                    ["Events", len(sp_events)],
                    ["Start share price", f"{sp_df['share_price'].iloc[0]:.6f}"],
                    ["End share price", f"{sp_df['share_price'].iloc[-1]:.6f}"],
                    ["Min share price", f"{sp_df['share_price'].min():.6f}"],
                    ["Max share price", f"{sp_df['share_price'].max():.6f}"],
                    ["End total assets", f"${sp_df['total_assets'].iloc[-1]:,.2f}"],
                    ["End total supply", f"{sp_df['total_supply'].iloc[-1]:,.4f}"],
                    ["Epoch resets", sp_df["epoch_reset"].sum()],
                ],
                tablefmt="simple",
            )
        )

        # Show last 10 share price events
        if len(sp_df) > 10:
            print(f"\nLast 10 share price events:")
            tail = sp_df.tail(10)
        else:
            tail = sp_df

        print(
            tabulate(
                [[str(idx), r["event_type"], f"${r['total_assets']:,.2f}", f"{r['total_supply']:,.4f}", f"{r['share_price']:.6f}", f"${r['delta']:,.2f}", r.get("coin", "")] for idx, r in tail.iterrows()],
                headers=["Timestamp", "Event", "Total assets", "Total supply", "Share price", "Delta", "Coin"],
                tablefmt="simple",
            )
        )
    else:
        print("No share price events computed (no deposits/fills/funding)")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
