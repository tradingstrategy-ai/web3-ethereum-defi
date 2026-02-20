"""Investigate Hypercore vaults with abnormal profit metrics.

Loads cleaned price data and vault metadata, runs calculate_lifetime_metrics()
for Hypercore (Hyperliquid native) vaults, and analyses the results
to identify vaults with suspicious or abnormal returns.

Root causes of abnormal profits (discovered during investigation):

1. **Share price overflow**: When total_supply approaches zero while total_assets
   remains nonzero in ``_calculate_share_price()``, share prices can reach
   trillions/quadrillions. Fixed by capping share price at 10,000 in
   ``combined_analysis.py``.

2. **Bypassed cleaning pipeline**: Hyperliquid data was merged into the cleaned
   Parquet AFTER the ERC-4626 cleaning stages (``clean_returns()``,
   ``clean_by_tvl()``), so outlier returns were never zeroed. Fixed by routing
   Hypercore data through the standard cleaning pipeline via
   ``merge_into_uncleaned_parquet()`` + ``process_raw_vault_scan_data()``.

3. **Short-age CAGR extrapolation**: A 612% return over 14 days extrapolates
   to a sextillion% CAGR via ``(1+r)^(365/days)``. Fixed by capping CAGR at
   10,000% in ``calculate_period_metrics()``.

4. **Leveraged trading vaults**: Hyperliquid vaults trade perpetual futures
   with leverage. Daily swings of 20-90% are normal for these, but
   fundamentally different from stablecoin yield vaults.

Filtering rules implemented:

- Share price > 10,000 at source → capped (``combined_analysis.py``)
- Hypercore share prices > 10,000 in cleaning → capped (``wrangle_vault_prices.py``)
- |daily return| > 50% in cleaning → zeroed (``wrangle_vault_prices.py``)
- max share price > 10,000 in metrics → blacklisted (``vault_metrics.py``)
- CAGR > 10,000% in period metrics → capped (``vault_metrics.py``)
- New ``abnormal_share_price`` flag in ``VaultFlag`` enum

Usage:

.. code-block:: shell

    poetry run python scripts/hyperliquid/vaults-with-abnormal-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``PARQUET_PATH``: Path to cleaned Parquet. Default: ~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet
- ``VAULT_DB_PATH``: Path to VaultDatabase pickle. Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``CAGR_THRESHOLD``: CAGR above which a vault is considered abnormal. Default: 1.0 (100%)
"""

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.research.vault_metrics import calculate_lifetime_metrics
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def main():
    # Configuration
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    parquet_path = Path(os.environ.get("PARQUET_PATH", str(DEFAULT_RAW_PRICE_DATABASE)))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE)))
    cagr_threshold = float(os.environ.get("CAGR_THRESHOLD", "1.0"))

    print("Hypercore vault abnormal metrics investigation")
    print(f"Parquet: {parquet_path}")
    print(f"VaultDB: {vault_db_path}")
    print(f"CAGR threshold: {cagr_threshold:.0%}")
    print()

    # Load data
    assert parquet_path.exists(), f"Parquet file not found: {parquet_path}"
    assert vault_db_path.exists(), f"VaultDB not found: {vault_db_path}"

    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(parquet_path)

    if not isinstance(prices_df.index, pd.DatetimeIndex):
        if "timestamp" in prices_df.columns:
            prices_df = prices_df.set_index("timestamp")

    # Filter for Hypercore vaults only
    hl_mask = prices_df["chain"] == HYPERCORE_CHAIN_ID
    hl_prices = prices_df[hl_mask].copy()

    hl_vault_count = hl_prices["id"].nunique()
    print(f"Total rows in cleaned parquet: {len(prices_df):,}")
    print(f"Hypercore rows: {len(hl_prices):,}")
    print(f"Hypercore vaults: {hl_vault_count}")
    print()

    if hl_vault_count == 0:
        print("No Hypercore vaults found in data.")
        return

    # --- Section 1: Raw price data analysis ---
    print("=" * 80)
    print("SECTION 1: Raw price data analysis")
    print("=" * 80)
    print()

    # Per-vault summary of raw price data
    raw_summary = hl_prices.groupby("id").agg(
        name=("name", "first"),
        rows=("share_price", "count"),
        first_date=("share_price", lambda x: x.index.min()),
        last_date=("share_price", lambda x: x.index.max()),
        share_price_start=("share_price", "first"),
        share_price_end=("share_price", "last"),
        share_price_min=("share_price", "min"),
        share_price_max=("share_price", "max"),
        tvl_start=("total_assets", "first"),
        tvl_end=("total_assets", "last"),
        tvl_min=("total_assets", "min"),
        tvl_max=("total_assets", "max"),
        max_daily_return=("returns_1h", "max"),
        min_daily_return=("returns_1h", "min"),
    )

    raw_summary["lifetime_return"] = (raw_summary["share_price_end"] / raw_summary["share_price_start"]) - 1
    raw_summary["days"] = (raw_summary["last_date"] - raw_summary["first_date"]).dt.days
    raw_summary["price_range_pct"] = (raw_summary["share_price_max"] / raw_summary["share_price_min"]) - 1

    # Sort by lifetime return descending
    raw_summary = raw_summary.sort_values("lifetime_return", ascending=False)

    print("Top 20 Hypercore vaults by lifetime return (from raw price data):")
    print()
    display_cols = [
        "name",
        "days",
        "rows",
        "share_price_start",
        "share_price_end",
        "lifetime_return",
        "tvl_end",
        "max_daily_return",
        "min_daily_return",
        "price_range_pct",
    ]
    top_df = raw_summary[display_cols].head(20).copy()
    top_df["lifetime_return"] = top_df["lifetime_return"].apply(lambda x: f"{x:.2%}")
    top_df["max_daily_return"] = top_df["max_daily_return"].apply(lambda x: f"{x:.2%}")
    top_df["min_daily_return"] = top_df["min_daily_return"].apply(lambda x: f"{x:.2%}")
    top_df["price_range_pct"] = top_df["price_range_pct"].apply(lambda x: f"{x:.2%}")
    top_df["tvl_end"] = top_df["tvl_end"].apply(lambda x: f"${x:,.0f}")
    top_df["share_price_start"] = top_df["share_price_start"].apply(lambda x: f"{x:.4f}")
    top_df["share_price_end"] = top_df["share_price_end"].apply(lambda x: f"{x:.4f}")

    print(tabulate(top_df, headers="keys", tablefmt="simple", showindex=True))
    print()

    # --- Section 2: Return distribution ---
    print("=" * 80)
    print("SECTION 2: Daily return distribution for Hypercore vaults")
    print("=" * 80)
    print()

    returns = hl_prices["returns_1h"].dropna()
    print(f"Total return observations: {len(returns):,}")
    print(f"Returns > 50%:  {(returns > 0.50).sum():,}")
    print(f"Returns > 20%:  {(returns > 0.20).sum():,}")
    print(f"Returns > 10%:  {(returns > 0.10).sum():,}")
    print(f"Returns > 5%:   {(returns > 0.05).sum():,}")
    print(f"Returns < -50%: {(returns < -0.50).sum():,}")
    print(f"Returns < -20%: {(returns < -0.20).sum():,}")
    print(f"Returns < -10%: {(returns < -0.10).sum():,}")
    print()

    # Show vaults with extreme single-day returns
    extreme_mask = returns.abs() > 0.20
    if extreme_mask.sum() > 0:
        extreme_rows = hl_prices.loc[extreme_mask.index[extreme_mask]]
        print(f"Rows with |daily return| > 20%: {len(extreme_rows)}")
        extreme_display = extreme_rows[["name", "id", "returns_1h", "share_price", "total_assets"]].copy()
        extreme_display = extreme_display.sort_values("returns_1h", ascending=False)
        extreme_display["returns_1h"] = extreme_display["returns_1h"].apply(lambda x: f"{x:.2%}")
        extreme_display["total_assets"] = extreme_display["total_assets"].apply(lambda x: f"${x:,.0f}")
        extreme_display["share_price"] = extreme_display["share_price"].apply(lambda x: f"{x:.4f}")
        print(tabulate(extreme_display.head(20), headers="keys", tablefmt="simple", showindex=True))
        print()

    # --- Section 3: TVL analysis ---
    print("=" * 80)
    print("SECTION 3: TVL analysis")
    print("=" * 80)
    print()

    tvl_summary = (
        hl_prices.groupby("id")
        .agg(
            name=("name", "first"),
            tvl_end=("total_assets", "last"),
            tvl_mean=("total_assets", "mean"),
            tvl_min=("total_assets", "min"),
            tvl_max=("total_assets", "max"),
        )
        .sort_values("tvl_end", ascending=False)
    )

    print(f"Vaults with TVL < $1,000: {(tvl_summary['tvl_end'] < 1000).sum()}")
    print(f"Vaults with TVL < $5,000: {(tvl_summary['tvl_end'] < 5000).sum()}")
    print(f"Vaults with TVL $5k-$50k: {((tvl_summary['tvl_end'] >= 5000) & (tvl_summary['tvl_end'] < 50000)).sum()}")
    print(f"Vaults with TVL $50k-$1M: {((tvl_summary['tvl_end'] >= 50000) & (tvl_summary['tvl_end'] < 1_000_000)).sum()}")
    print(f"Vaults with TVL > $1M: {(tvl_summary['tvl_end'] >= 1_000_000).sum()}")
    print()

    # --- Section 4: Run calculate_lifetime_metrics ---
    print("=" * 80)
    print("SECTION 4: calculate_lifetime_metrics() results")
    print("=" * 80)
    print()

    # Filter vault_db to Hypercore vaults only
    hl_vault_specs = {spec for spec in vault_db.rows if spec.chain_id == HYPERCORE_CHAIN_ID}
    hl_vault_rows = {spec: vault_db.rows[spec] for spec in hl_vault_specs}

    print(f"Hypercore vaults in VaultDB: {len(hl_vault_rows)}")
    print(f"Hypercore vaults in price data: {hl_vault_count}")
    print("Running calculate_lifetime_metrics()...")
    print()

    try:
        metrics_df = calculate_lifetime_metrics(hl_prices, hl_vault_rows)
    except Exception as e:
        print(f"Error running calculate_lifetime_metrics(): {e}")
        import traceback

        traceback.print_exc()
        return

    print(f"Metrics calculated for {len(metrics_df)} vaults")
    print()

    # Sort by CAGR
    metrics_df = metrics_df.sort_values("cagr", ascending=False, na_position="last")

    # Display top vaults by CAGR
    print("Top 30 Hypercore vaults by CAGR (gross):")
    print()
    metric_cols = [
        "name",
        "cagr",
        "lifetime_return",
        "current_nav",
        "years",
        "last_share_price",
        "three_months_cagr",
        "one_month_cagr",
        "risk",
    ]
    available_cols = [c for c in metric_cols if c in metrics_df.columns]
    top_metrics = metrics_df[available_cols].head(30).copy()

    for col in ["cagr", "lifetime_return", "three_months_cagr", "one_month_cagr"]:
        if col in top_metrics.columns:
            top_metrics[col] = top_metrics[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) and x != 0 else "N/A")
    if "current_nav" in top_metrics.columns:
        top_metrics["current_nav"] = top_metrics["current_nav"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "N/A")
    if "last_share_price" in top_metrics.columns:
        top_metrics["last_share_price"] = top_metrics["last_share_price"].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "N/A")

    print(tabulate(top_metrics, headers="keys", tablefmt="simple", showindex=False))
    print()

    # --- Section 5: Abnormal profit analysis ---
    print("=" * 80)
    print("SECTION 5: Abnormal profit analysis")
    print("=" * 80)
    print()

    abnormal_mask = metrics_df["cagr"].apply(lambda x: x > cagr_threshold if pd.notna(x) else False)
    abnormal_count = abnormal_mask.sum()
    total_count = len(metrics_df)

    print(f"Vaults with CAGR > {cagr_threshold:.0%}: {abnormal_count} / {total_count}")
    print()

    if abnormal_count > 0:
        abnormal_df = metrics_df[abnormal_mask].copy()

        # Analyse patterns in abnormal vaults
        print("Pattern analysis of abnormal vaults:")
        print(f"  Mean TVL: ${abnormal_df['current_nav'].mean():,.0f}")
        print(f"  Median TVL: ${abnormal_df['current_nav'].median():,.0f}")
        print(f"  Mean age (years): {abnormal_df['years'].mean():.2f}")
        print(f"  Median age (years): {abnormal_df['years'].median():.2f}")
        print()

        normal_df = metrics_df[~abnormal_mask & (metrics_df["cagr"].notna()) & (metrics_df["cagr"] != 0)]
        if len(normal_df) > 0:
            print("Pattern analysis of normal vaults:")
            print(f"  Mean TVL: ${normal_df['current_nav'].mean():,.0f}")
            print(f"  Median TVL: ${normal_df['current_nav'].median():,.0f}")
            print(f"  Mean age (years): {normal_df['years'].mean():.2f}")
            print(f"  Median age (years): {normal_df['years'].median():.2f}")
            print()

    # --- Section 6: Proposed filtering rules ---
    print("=" * 80)
    print("SECTION 6: Proposed filtering rules analysis")
    print("=" * 80)
    print()

    # Analyse what percentage would be filtered by different rules
    rules = {
        "CAGR > 100%": metrics_df["cagr"].apply(lambda x: x > 1.0 if pd.notna(x) else False),
        "CAGR > 200%": metrics_df["cagr"].apply(lambda x: x > 2.0 if pd.notna(x) else False),
        "CAGR > 500%": metrics_df["cagr"].apply(lambda x: x > 5.0 if pd.notna(x) else False),
        "Lifetime return > 100%": metrics_df["lifetime_return"].apply(lambda x: x > 1.0 if pd.notna(x) else False),
        "TVL < $5,000": metrics_df["current_nav"] < 5000,
        "TVL < $10,000": metrics_df["current_nav"] < 10000,
        "Age < 30 days": metrics_df["years"] < 30 / 365.25,
        "Age < 7 days": metrics_df["years"] < 7 / 365.25,
    }

    rule_results = []
    for rule_name, mask in rules.items():
        count = mask.sum()
        pct = count / total_count if total_count > 0 else 0
        rule_results.append({"Rule": rule_name, "Filtered": count, "% of total": f"{pct:.1%}"})

    print(tabulate(rule_results, headers="keys", tablefmt="simple"))
    print()

    # Cross-analysis: CAGR by TVL bucket
    print("CAGR distribution by TVL bucket:")
    print()

    tvl_bins = [0, 1000, 5000, 10000, 50000, 100000, 1_000_000, float("inf")]
    tvl_labels = ["<$1k", "$1k-$5k", "$5k-$10k", "$10k-$50k", "$50k-$100k", "$100k-$1M", ">$1M"]
    metrics_df["tvl_bucket"] = pd.cut(metrics_df["current_nav"], bins=tvl_bins, labels=tvl_labels)

    tvl_cagr = (
        metrics_df.groupby("tvl_bucket", observed=True)
        .agg(
            count=("cagr", "count"),
            median_cagr=("cagr", "median"),
            mean_cagr=("cagr", "mean"),
            max_cagr=("cagr", "max"),
            abnormal_count=("cagr", lambda x: (x > cagr_threshold).sum()),
        )
        .reset_index()
    )

    tvl_cagr["median_cagr"] = tvl_cagr["median_cagr"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "N/A")
    tvl_cagr["mean_cagr"] = tvl_cagr["mean_cagr"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "N/A")
    tvl_cagr["max_cagr"] = tvl_cagr["max_cagr"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "N/A")

    print(tabulate(tvl_cagr, headers="keys", tablefmt="simple", showindex=False))
    print()

    # --- Section 7: Deep dive into worst offenders ---
    print("=" * 80)
    print("SECTION 7: Deep dive into top 10 abnormal vaults")
    print("=" * 80)
    print()

    top_abnormal = metrics_df.head(10)
    for _, row in top_abnormal.iterrows():
        vault_id = row["id"]
        vault_prices = hl_prices[hl_prices["id"] == vault_id].sort_index()

        print(f"Vault: {row['name']}")
        print(f"  ID: {vault_id}")
        print(f"  CAGR: {row['cagr']:.2%}" if pd.notna(row["cagr"]) else "  CAGR: N/A")
        print(f"  Lifetime return: {row['lifetime_return']:.2%}" if pd.notna(row["lifetime_return"]) else "  Lifetime return: N/A")
        print(f"  TVL: ${row['current_nav']:,.0f}")
        print(f"  Age: {row['years']:.2f} years ({row['years'] * 365.25:.0f} days)")
        print(f"  Data points: {len(vault_prices)}")

        if len(vault_prices) > 0:
            sp = vault_prices["share_price"]
            ret = vault_prices["returns_1h"].dropna()
            print(f"  Share price: {sp.iloc[0]:.4f} -> {sp.iloc[-1]:.4f}")
            print(f"  Share price min/max: {sp.min():.4f} / {sp.max():.4f}")
            print(f"  Max single-day return: {ret.max():.2%}" if len(ret) > 0 else "  Max single-day return: N/A")
            print(f"  Min single-day return: {ret.min():.2%}" if len(ret) > 0 else "  Min single-day return: N/A")
            print(f"  Returns > 10%: {(ret > 0.10).sum()}")
            print(f"  Returns < -10%: {(ret < -0.10).sum()}")

            # Check for share price jumps
            sp_pct_change = sp.pct_change().dropna()
            big_jumps = sp_pct_change[sp_pct_change.abs() > 0.10]
            if len(big_jumps) > 0:
                print(f"  Share price jumps > 10%: {len(big_jumps)}")
                for ts, jump in big_jumps.items():
                    print(f"    {ts}: {jump:.2%}")

        print()

    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
