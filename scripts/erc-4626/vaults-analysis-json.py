#!/usr/bin/env python
"""
Vault analysis script to generate top vaults for all chains.
Output: top_vaults_by_chain.json (no Google Sheets upload).
"""

from __future__ import annotations

import os
import argparse
import pickle
import warnings
import json
from pathlib import Path
from datetime import date  # for timestamp

import pandas as pd

from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    clean_lifetime_metrics,
    cross_check_data,
    format_lifetime_table,
)
from eth_defi.token import is_stablecoin_like

# Default locations for input and output files
DEFAULT_OUTPUT_FOLDER = Path("~/.tradingstrategy/vaults").expanduser()
DEFAULT_JSON_OUTPUT = Path("~/.tradingstrategy/top_vaults_by_chain.json")

# -------------------------
# Helper Functions
# -------------------------

def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the script.

    --output-folder: path to the folder containing cleaned vault data files.
    --output-json: path where the resulting JSON should be written.
    """
    parser = argparse.ArgumentParser(description="Generate top vaults JSON")
    parser.add_argument("--output-folder", type=Path, default=DEFAULT_OUTPUT_FOLDER)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()

def load_data(output_folder: Path) -> tuple[pd.DataFrame, dict]:
    """
    Load vault price time series and vault metadata from disk.

    Reads:
    - cleaned-vault-prices-1h.parquet (hourly price data)
    - vault-db.pickle (metadata for each vault)
    """
    parquet_file = output_folder / "cleaned-vault-prices-1h.parquet"
    vault_db_file = output_folder / "vault-db.pickle"
    if not parquet_file.exists() or not vault_db_file.exists():
        raise FileNotFoundError("Missing vault data files")
    prices_df = pd.read_parquet(parquet_file)
    with vault_db_file.open("rb") as f:
        vault_db = pickle.load(f)
    return prices_df, vault_db

def summarise_prices(prices_df: pd.DataFrame, vault_db: dict) -> None:
    """
    Print a quick summary of loaded data and run a cross-check between 
    price data and vault metadata for consistency.
    """
    print(f"Loaded {len(prices_df):,} rows for {len(vault_db)} vaults on {prices_df['chain'].nunique()} chains")
    errors = cross_check_data(vault_db, prices_df)
    if errors:
        raise ValueError(f"Cross-check failed: {errors}")

def filter_stablecoin_vaults(prices_df: pd.DataFrame, vault_db: dict) -> tuple[pd.DataFrame, list]:
    """
    Filter only vaults denominated in stablecoins.

    Returns:
    - filtered prices_df containing only stablecoin vaults
    - list of stablecoin vault metadata objects
    """
    usd_vaults = [v for v in vault_db.values() if is_stablecoin_like(v["Denomination"])]
    allowed_ids = {
        f"{v['_detection_data'].chain}-{v['_detection_data'].address}" for v in usd_vaults
    }
    filtered_df = prices_df[prices_df["id"].isin(allowed_ids)].copy()
    return filtered_df, usd_vaults

def calculate_returns(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate hourly returns and derived metrics for all vaults in the given DataFrame.
    """
    return calculate_hourly_returns_for_all_vaults(prices_df)

def calculate_lifetime_data(returns_df: pd.DataFrame, vault_db: dict) -> pd.DataFrame:
    """
    Calculate lifetime performance metrics for each vault:
    - total returns
    - CAGR metrics
    - NAV thresholds

    Cleans the resulting DataFrame using clean_lifetime_metrics.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        df = calculate_lifetime_metrics(returns_df, vault_db)
    return clean_lifetime_metrics(df)

def apply_thresholds(df: pd.DataFrame, nav_threshold=50_000, event_threshold=5, sort_column="one_month_cagr") -> pd.DataFrame:
    """
    Filter vaults by NAV and event count thresholds, then sort by the specified column.

    nav_threshold: minimum current NAV (USD)
    event_threshold: minimum deposit/redeem events
    sort_column: column to sort descending by
    """
    df = df[(df["current_nav"] >= nav_threshold) & (df["event_count"] >= event_threshold)]
    return df.sort_values(by=sort_column, ascending=False)

def prepare_output_table(filtered_df: pd.DataFrame) -> pd.DataFrame:
    """
    Format the filtered vault DataFrame into a table suitable for export.

    - Takes top 30 vaults
    - Adds 'Vault' column if missing (from 'name' column)
    - Restricts to a known set of columns for consistent output
    """
    formatted = format_lifetime_table(filtered_df.head(30)).copy()
    if "Vault" not in formatted.columns and "name" in filtered_df.columns:
        formatted["Vault"] = filtered_df["name"].values[: len(formatted)]
    final_cols = [
        "Vault", "1M return", "1M return ann.", "3M return ann.",
        "Lifetime return", "Current TVL USD", "Denomination",
        "Chain", "Protocol", "Management fee", "Performance fee",
    ]
    for col in final_cols:
        if col not in formatted.columns:
            raise KeyError(f"Missing expected column: {col}")
    return formatted[final_cols]

# -------------------------
# Main Execution
# -------------------------

def main():
    """
    Main entry point for vault analysis.

    Steps:
    - Load vault data
    - Filter to stablecoin vaults
    - For each chain, calculate returns & lifetime metrics
    - Apply thresholds and prepare top vaults table
    - Combine all chains and export as JSON with timestamp
    """
    args = parse_args()
    prices_df, vault_db = load_data(args.output_folder)
    summarise_prices(prices_df, vault_db)

    # Only include stablecoin vaults
    prices_df, _ = filter_stablecoin_vaults(prices_df, vault_db)

    # Process each chain separately
    result = {}
    for chain_id in sorted(prices_df["chain"].unique()):
        print(f"\n🔍 Processing chain {chain_id}...")
        chain_df = prices_df[prices_df["chain"] == chain_id].copy()
        returns_df = calculate_returns(chain_df)
        lifetime_df = calculate_lifetime_data(returns_df, vault_db)
        filtered_df = apply_thresholds(lifetime_df)
        final_df = prepare_output_table(filtered_df)
        result[str(chain_id)] = final_df.to_dict(orient="records")

    # Add export date (only the date part)
    output = {
        "generated_at": date.today().isoformat(),  # e.g. "2025-10-07"
        "chains": result,
    }

    # Save final JSON
    with open(args.output_json, "w") as f:
        json.dump(output, f, indent=2)
        print(f"\n✅ Exported to {args.output_json} with timestamp {output['generated_at']}")

if __name__ == "__main__":
    main()
