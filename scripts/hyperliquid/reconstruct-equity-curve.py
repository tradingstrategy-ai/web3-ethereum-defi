"""Reconstruct and visualise equity curve for a Hyperliquid account.

Reads fills, funding payments, and ledger events from the local trade
history DuckDB, reconstructs PnL and account value curves, and opens
a Plotly dashboard in the browser.

For vault addresses, also shows event-accurate share price and total
supply curves. This provides a more accurate alternative to the daily
metrics pipeline.

Usage:

.. code-block:: shell

    # Basic usage (trader account)
    ADDRESS=0x162cc7c861ebd0c06b3d72319201150482518185 \\
      poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

    # With custom database path
    ADDRESS=0x162cc7c861ebd0c06b3d72319201150482518185 \\
      TRADE_HISTORY_DB_PATH=~/my-trade-history.duckdb \\
      poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

    # Generate chart without opening browser (for testing)
    ADDRESS=0x162cc7c861ebd0c06b3d72319201150482518185 \\
      NO_BROWSER=true \\
      poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

Environment variables:

- ``ADDRESS``: Hyperliquid account address (required).
- ``TRADE_HISTORY_DB_PATH``: Path to trade history DuckDB.
  Default: ~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb
- ``LOG_LEVEL``: Logging level. Default: warning.
- ``NO_BROWSER``: Set to ``true`` to generate the HTML chart without
  opening the browser. Useful for testing.

Migrating stale is_vault flags
-------------------------------

If your trade history database was populated before the ``is_vault``
auto-detection fix, some vault accounts may have ``is_vault=FALSE``
in the ``accounts`` table. The equity curve reconstruction handles
this transparently via :py:meth:`~eth_defi.hyperliquid.trade_history_db.HyperliquidTradeHistoryDatabase.is_vault_address`,
which detects vaults from ledger events at read time.

To proactively fix stale flags, run this one-off SQL against your
DuckDB:

.. code-block:: sql

    UPDATE accounts SET is_vault = TRUE
    WHERE address IN (
        SELECT DISTINCT address FROM ledger
        WHERE event_type IN (
            'vaultCreate', 'vaultDeposit', 'vaultWithdraw',
            'vaultDistribution', 'vaultLeaderCommission'
        )
    );

You can execute this via the DuckDB CLI::

    duckdb ~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb < migrate.sql

Or inline::

    duckdb ~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb -c "UPDATE accounts SET is_vault = TRUE WHERE address IN (SELECT DISTINCT address FROM ledger WHERE event_type IN ('vaultCreate','vaultDeposit','vaultWithdraw','vaultDistribution','vaultLeaderCommission'))"
"""

import logging
import os
import tempfile
import webbrowser
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.equity_curve_reconstruction import (
    create_equity_curve_figure,
    reconstruct_equity_curve,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.trade_history_db import (
    DEFAULT_TRADE_HISTORY_DB_PATH,
    HyperliquidTradeHistoryDatabase,
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
    no_browser = os.environ.get("NO_BROWSER", "").lower() in ("true", "1", "yes")

    db_path = Path(os.environ.get("TRADE_HISTORY_DB_PATH", str(DEFAULT_TRADE_HISTORY_DB_PATH))).expanduser()

    if not db_path.exists():
        print(f"ERROR: Trade history database not found: {db_path}")
        print("Run sync-trade-history.py first to populate the database.")
        return

    print(f"Hyperliquid equity curve reconstruction")
    print(f"Address: {address}")
    print(f"Database: {db_path}")

    db = HyperliquidTradeHistoryDatabase(db_path)
    session = create_hyperliquid_session()

    try:
        data = reconstruct_equity_curve(db, address, session=session)

        if data is None:
            print(f"\nAddress {address} not found in trade history database.")
            print("Add it with sync-trade-history.py first.")
            return

        if data.fill_count == 0 and data.funding_count == 0:
            print(f"\nNo fills or funding data found for {address}.")
            print("Run sync-trade-history.py to sync data for this address.")
            return

        # Summary table
        account_type = "Vault" if data.is_vault else "Trader"
        summary_rows = [
            ["Account type", account_type],
            ["Label", data.label or "—"],
            ["Fills", f"{data.fill_count:,}"],
            ["Funding payments", f"{data.funding_count:,}"],
            ["Ledger events", f"{data.ledger_count:,}"],
        ]

        if data.data_start_at is not None:
            summary_rows.append(["Data starts from", str(data.data_start_at)])

        if not data.pnl_curve.empty:
            first_ts = data.pnl_curve.index[0]
            last_ts = data.pnl_curve.index[-1]
            summary_rows.append(["Date range", f"{first_ts} to {last_ts}"])

            final_pnl = data.pnl_curve.iloc[-1]
            summary_rows.extend(
                [
                    ["Cumulative closed PnL", f"${final_pnl['cumulative_closed_pnl']:,.2f}"],
                    ["Cumulative funding PnL", f"${final_pnl['cumulative_funding_pnl']:,.2f}"],
                    ["Cumulative fees", f"${final_pnl['cumulative_fees']:,.2f}"],
                    ["Net PnL", f"${final_pnl['cumulative_net_pnl']:,.2f}"],
                ]
            )

        print(f"\n{tabulate(summary_rows, tablefmt='simple')}")

        # Vault share price stats
        if data.share_price_curve is not None and not data.share_price_curve.empty:
            sp = data.share_price_curve
            sp_rows = [
                ["Start share price", f"{sp['share_price'].iloc[0]:.6f}"],
                ["End share price", f"{sp['share_price'].iloc[-1]:.6f}"],
                ["Min share price", f"{sp['share_price'].min():.6f}"],
                ["Max share price", f"{sp['share_price'].max():.6f}"],
                ["End total assets", f"${sp['total_assets'].iloc[-1]:,.2f}"],
                ["End total supply", f"{sp['total_supply'].iloc[-1]:,.4f}"],
                ["Epoch resets", int(sp["epoch_reset"].sum())],
                ["Share price events", len(sp)],
            ]
            print(f"\nShare price")
            print(tabulate(sp_rows, tablefmt="simple"))

        # Create and display Plotly figure
        import eth_defi.monkeypatch.plotly  # noqa: F401

        fig = create_equity_curve_figure(data)

        html_path = Path(tempfile.gettempdir()) / f"equity-curve-{address[:10]}.html"
        fig.write_html(str(html_path))
        print(f"\nChart saved to: {html_path}")

        if not no_browser:
            webbrowser.open(f"file://{html_path}")

    finally:
        db.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
