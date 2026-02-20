"""Isolated GRVT pipeline test — fetch all vaults, compute metrics, inspect data.

Runs the GRVT pipeline from scratch using temporary database locations.
Fetches all discoverable vaults, their share price history, and computes
lifetime metrics (TVL, 1M CAGR, 3M CAGR, Sharpe) from the daily price data.

Compares our computed metrics against the GRVT API-reported values to
verify data quality.

Usage:

.. code-block:: shell

    LOG_LEVEL=info poetry run python scripts/grvt/import-grvt-isolated.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info

"""

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from tabulate import tabulate

from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase, fetch_and_store_vault
from eth_defi.grvt.vault import (
    fetch_vault_details,
    fetch_vault_listing,
    fetch_vault_performance,
    fetch_vault_risk_metrics,
    fetch_vault_summary_history,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def compute_cagr(start_price: float, end_price: float, days: int) -> float | None:
    """Compute annualised CAGR from start/end prices over a number of days."""
    if days <= 0 or start_price <= 0 or end_price <= 0:
        return None
    years = days / 365.0
    if years < 0.001:
        return None
    base = end_price / start_price
    # Cap at 100x to prevent astronomical extrapolations
    cagr = min(base ** (1.0 / years) - 1.0, 100.0)
    return cagr


def compute_sharpe(daily_returns: pd.Series) -> float | None:
    """Compute annualised Sharpe ratio from daily returns."""
    clean = daily_returns.dropna()
    if len(clean) < 7:
        return None
    mean_r = clean.mean()
    std_r = clean.std()
    if std_r < 1e-12:
        return None
    return (mean_r / std_r) * np.sqrt(365)


def compute_max_drawdown(prices: pd.Series) -> float | None:
    """Compute max drawdown from a price series."""
    if len(prices) < 2:
        return None
    running_max = prices.cummax()
    drawdown = (prices - running_max) / running_max
    return float(drawdown.min())


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=default_log_level)

    session = requests.Session()

    # Step 1: Discover vaults
    print("=" * 80)
    print("GRVT isolated pipeline test")
    print("=" * 80)

    print("\n--- Step 1: Discovering vaults from strategies page ---")
    vaults = fetch_vault_listing(session, only_discoverable=True)
    print(f"Found {len(vaults)} discoverable vaults")

    for v in vaults:
        cats = ", ".join(v.categories) if v.categories else "-"
        age = ""
        if v.create_time:
            age_days = (pd.Timestamp.now() - pd.Timestamp(v.create_time)).days
            age = f" (age: {age_days}d)"
        print(f"  {v.name:30s} chain_id={v.chain_vault_id:>12d}  type={v.vault_type:10s}  categories=[{cats}]{age}")

    # Step 2: Fetch API-reported metrics
    chain_ids = [v.chain_vault_id for v in vaults]

    print("\n--- Step 2: Fetching API-reported metrics ---")
    details = fetch_vault_details(session, chain_ids)
    perf = fetch_vault_performance(session, chain_ids)
    risk = fetch_vault_risk_metrics(session, chain_ids)

    print(f"  Details: {len(details)} vaults")
    print(f"  Performance: {len(perf)} vaults")
    print(f"  Risk metrics: {len(risk)} vaults")

    # Step 3: Fetch share price history and store in temp DuckDB
    print("\n--- Step 3: Fetching share price history per vault ---")
    tmp_dir = tempfile.mkdtemp(prefix="grvt-isolated-")
    db_path = Path(tmp_dir) / "grvt-test.duckdb"
    db = GRVTDailyMetricsDatabase(db_path)

    # Enrich summaries with TVL
    for v in vaults:
        d = details.get(v.chain_vault_id)
        if d:
            v.tvl = float(d.get("total_equity", 0) or 0)
            v.share_price = float(d.get("share_price", 0) or 0)

    history_data: dict[str, pd.DataFrame] = {}
    for v in vaults:
        try:
            daily_df = fetch_vault_summary_history(session, v.chain_vault_id)
        except Exception as e:
            logger.warning("Failed history for %s: %s", v.name, e)
            daily_df = pd.DataFrame()

        if not daily_df.empty:
            history_data[v.vault_id] = daily_df
            fetch_and_store_vault(session, db, v)
            first = daily_df.index[0].strftime("%Y-%m-%d")
            last = daily_df.index[-1].strftime("%Y-%m-%d")
            print(f"  {v.name:30s}  {len(daily_df):4d} days  [{first} .. {last}]")
        else:
            print(f"  {v.name:30s}  NO DATA")

    db.save()

    # Step 4: Compute metrics from share price history
    print("\n--- Step 4: Computing metrics ---")
    now = pd.Timestamp.now()

    rows = []
    for v in vaults:
        cid = v.chain_vault_id
        daily_df = history_data.get(v.vault_id)

        # API-reported values
        api_perf = perf.get(cid)
        api_risk = risk.get(cid)

        # Computed values from history
        computed_1m_cagr = None
        computed_3m_cagr = None
        computed_lifetime_cagr = None
        computed_sharpe = None
        computed_max_dd = None
        history_days = 0
        daily_returns = None

        if daily_df is not None and len(daily_df) >= 2:
            history_days = len(daily_df)
            prices = daily_df["share_price"]
            daily_returns = daily_df["daily_return"]

            # Lifetime CAGR
            computed_lifetime_cagr = compute_cagr(prices.iloc[0], prices.iloc[-1], history_days)

            # 1M CAGR (last 30 days)
            cutoff_1m = now - pd.Timedelta(days=30)
            mask_1m = daily_df.index >= cutoff_1m
            if mask_1m.sum() >= 2:
                p1m = prices[mask_1m]
                computed_1m_cagr = compute_cagr(p1m.iloc[0], p1m.iloc[-1], len(p1m))

            # 3M CAGR (last 90 days)
            cutoff_3m = now - pd.Timedelta(days=90)
            mask_3m = daily_df.index >= cutoff_3m
            if mask_3m.sum() >= 2:
                p3m = prices[mask_3m]
                computed_3m_cagr = compute_cagr(p3m.iloc[0], p3m.iloc[-1], len(p3m))

            # Sharpe from daily returns
            computed_sharpe = compute_sharpe(daily_returns)

            # Max drawdown
            computed_max_dd = compute_max_drawdown(prices)

        row = {
            "Name": v.name[:25],
            "TVL": v.tvl,
            "Days": history_days,
            "Share px": v.share_price,
            "1M CAGR": computed_1m_cagr,
            "3M CAGR": computed_3m_cagr,
            "Life CAGR": computed_lifetime_cagr,
            "Sharpe": computed_sharpe,
            "Max DD": computed_max_dd,
            "API APR": api_perf.apr if api_perf else None,
            "API 30d": api_perf.return_30d if api_perf else None,
            "API 90d": api_perf.return_90d if api_perf else None,
            "API Sharpe": api_risk.sharpe_ratio if api_risk else None,
            "API MaxDD": api_risk.max_drawdown if api_risk else None,
        }
        rows.append(row)

    # Step 5: Display results
    print("\n" + "=" * 80)
    print("GRVT vault metrics — computed from share price history vs API-reported")
    print("=" * 80)

    display_rows = []
    for r in rows:
        display_rows.append(
            {
                "Name": r["Name"],
                "TVL": f"${r['TVL']:,.0f}" if r["TVL"] else "-",
                "Days": r["Days"],
                "Px": f"{r['Share px']:.4f}" if r["Share px"] else "-",
                "1M CAGR": f"{r['1M CAGR'] * 100:.1f}%" if r["1M CAGR"] is not None else "-",
                "3M CAGR": f"{r['3M CAGR'] * 100:.1f}%" if r["3M CAGR"] is not None else "-",
                "Life CAGR": f"{r['Life CAGR'] * 100:.1f}%" if r["Life CAGR"] is not None else "-",
                "Sharpe": f"{r['Sharpe']:.2f}" if r["Sharpe"] is not None else "-",
                "MaxDD": f"{r['Max DD'] * 100:.1f}%" if r["Max DD"] is not None else "-",
            }
        )

    print("\nComputed metrics (from share price history):")
    print(tabulate(display_rows, headers="keys", tablefmt="fancy_grid"))

    # API comparison table
    print("\nAPI-reported metrics:")
    api_rows = []
    for r in rows:
        api_rows.append(
            {
                "Name": r["Name"],
                "API APR": f"{r['API APR'] * 100:.1f}%" if r["API APR"] else "-",
                "API 30d ret": f"{r['API 30d'] * 100:.2f}%" if r["API 30d"] else "-",
                "API 90d ret": f"{r['API 90d'] * 100:.2f}%" if r["API 90d"] else "-",
                "API Sharpe": f"{r['API Sharpe']:.2f}" if r["API Sharpe"] else "-",
                "API MaxDD": f"{r['API MaxDD'] * 100:.1f}%" if r["API MaxDD"] else "-",
            }
        )
    print(tabulate(api_rows, headers="keys", tablefmt="fancy_grid"))

    # Step 6: Anomaly checks
    print("\n" + "=" * 80)
    print("Data quality checks")
    print("=" * 80)

    anomalies = []
    for r in rows:
        name = r["Name"]

        if r["Days"] == 0:
            anomalies.append(f"  [WARN] {name}: No share price history available")
            continue

        if r["Days"] < 7:
            anomalies.append(f"  [WARN] {name}: Very short history ({r['Days']} days)")

        if r["Share px"] and (r["Share px"] < 0.5 or r["Share px"] > 2.0):
            anomalies.append(f"  [INFO] {name}: Share price {r['Share px']:.4f} is far from 1.0")

        if r["1M CAGR"] is not None and abs(r["1M CAGR"]) > 5.0:
            anomalies.append(f"  [WARN] {name}: 1M CAGR {r['1M CAGR'] * 100:.0f}% looks extreme")

        if r["Max DD"] is not None and r["Max DD"] < -0.5:
            anomalies.append(f"  [WARN] {name}: Max drawdown {r['Max DD'] * 100:.1f}% is severe")

        if r["TVL"] and r["TVL"] < 1000:
            anomalies.append(f"  [WARN] {name}: Very low TVL (${r['TVL']:,.0f})")

        # Compare computed vs API sharpe
        if r["Sharpe"] is not None and r["API Sharpe"] is not None:
            diff = abs(r["Sharpe"] - r["API Sharpe"])
            if diff > 2.0:
                anomalies.append(f"  [INFO] {name}: Sharpe mismatch — computed={r['Sharpe']:.2f} vs API={r['API Sharpe']:.2f}")

    if anomalies:
        for a in anomalies:
            print(a)
    else:
        print("  No anomalies detected")

    # Summary stats
    vaults_with_data = sum(1 for r in rows if r["Days"] > 0)
    total_days = sum(r["Days"] for r in rows)
    total_tvl = sum(r["TVL"] for r in rows if r["TVL"])

    print(f"\n--- Summary ---")
    print(f"  Vaults with history: {vaults_with_data}/{len(rows)}")
    print(f"  Total data points:   {total_days:,}")
    print(f"  Total TVL:           ${total_tvl:,.0f}")
    print(f"  Temp database:       {db_path}")

    db.close()
    print("\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
