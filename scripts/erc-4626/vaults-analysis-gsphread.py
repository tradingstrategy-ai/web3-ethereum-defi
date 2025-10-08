#!/usr/bin/env python
"""Vault analysis script with CHAIN_ID and Google Sheet config from env."""

from __future__ import annotations

import os
import argparse
import pickle
import warnings
from pathlib import Path

import pandas as pd

from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    clean_lifetime_metrics,
    cross_check_data,
    format_lifetime_table,
)
from eth_defi.token import is_stablecoin_like

# Optional Google Sheets integration
try:
    import gspread
    from gspread_dataframe import set_with_dataframe
    from google.oauth2.service_account import Credentials
except ImportError as exc:
    gspread = None
    set_with_dataframe = None
    ServiceAccountCredentials = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

# -------------------------
# Defaults
# -------------------------

# Default paths and URLs if no env vars are set
DEFAULT_OUTPUT_FOLDER = Path("~/.tradingstrategy/vaults").expanduser()
DEFAULT_SERVICE_ACCOUNT = "~/.tradingstrategy/vaults/vault-export.json"
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/xxxxxxxxxxxxxxxxxxxxx"

# -------------------------
# Helper functions
# -------------------------

def get_env_or_default(key: str, default: str) -> str:
    """Get environment variable or return a fallback default."""
    return os.environ.get(key, default)

def get_chain_id_from_env() -> int:
    """Get CHAIN_ID from environment and cast to int. Default: 42161 (Arbitrum)."""
    try:
        return int(os.environ.get("CHAIN_ID", "42161"))
    except ValueError:
        raise RuntimeError("Invalid CHAIN_ID in environment (must be int)")

def get_worksheet_name(chain_id: int) -> str:
    """
    Return the worksheet name based on CHAIN_ID.
    Uses env override if available, otherwise falls back to default map.
    """
    chain_map = {
        42161: "Arbitrum-vault-data",
        8453: "Base-vault-data",
    }
    return os.environ.get("WORKSHEET", chain_map.get(chain_id, f"Chain-{chain_id}-vault-data"))

# -------------------------
# CLI argument parsing
# -------------------------

def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the script."""
    parser = argparse.ArgumentParser(description="Run vault analysis")
    parser.add_argument("--output-folder", type=Path, default=DEFAULT_OUTPUT_FOLDER)
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--service-account-file", type=Path, default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument("--sheet-url", default=DEFAULT_SHEET_URL)
    return parser.parse_args()

# -------------------------
# Vault data processing
# -------------------------

def load_data(output_folder: Path) -> tuple[pd.DataFrame, dict]:
    """
    Load vault data files from disk:
    - Parquet file: cleaned-vault-prices-1h.parquet
    - Pickle file: vault-db.pickle
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
    Print basic summary and run integrity check between vault metadata and price data.
    """
    print(f"Loaded {len(prices_df):,} rows for {len(vault_db)} vaults on {prices_df['chain'].nunique()} chains")
    errors = cross_check_data(vault_db, prices_df)
    if errors:
        raise ValueError(f"Cross-check failed: {errors}")

def filter_stablecoin_vaults(prices_df: pd.DataFrame, vault_db: dict) -> tuple[pd.DataFrame, list]:
    """
    Filter vaults to only include stablecoin-denominated ones.

    Returns:
    - prices_df with only stablecoin vaults
    - list of stablecoin vaults (metadata)
    """
    usd_vaults = [v for v in vault_db.values() if is_stablecoin_like(v["Denomination"])]
    allowed_ids = {
        f"{v['_detection_data'].chain}-{v['_detection_data'].address}" for v in usd_vaults
    }
    filtered_df = prices_df[prices_df["id"].isin(allowed_ids)].copy()
    print(f"Filtered to {len(filtered_df):,} stablecoin vault rows")
    return filtered_df, usd_vaults

def filter_chain(prices_df: pd.DataFrame, chain_id: int) -> pd.DataFrame:
    """
    Filter vault data by chain ID from environment.
    """
    chain_df = prices_df[prices_df["chain"] == chain_id].copy()
    print(f"{len(chain_df):,} rows for CHAIN_ID={chain_id}")
    return chain_df

def calculate_returns(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate hourly returns and rolling returns for all vaults.
    """
    return calculate_hourly_returns_for_all_vaults(prices_df)

def calculate_lifetime_data(returns_df: pd.DataFrame, vault_db: dict) -> pd.DataFrame:
    """
    Compute lifetime metrics from hourly returns.
    Suppress warnings during computation for cleaner logs.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        df = calculate_lifetime_metrics(returns_df, vault_db)
    return clean_lifetime_metrics(df)

def apply_thresholds(df: pd.DataFrame, nav_threshold=50_000, event_threshold=5, sort_column="one_month_cagr") -> pd.DataFrame:
    """
    Filter vaults based on thresholds and sort by specified return metric.

    Default:
    - NAV >= 500k USD
    - Event count >= 5
    - Sorted by 1-month annualised return
    """
    df = df[(df["current_nav"] >= nav_threshold) & (df["event_count"] >= event_threshold)]
    print(f"{len(df):,} vaults after filtering thresholds")
    return df.sort_values(by=sort_column, ascending=False)

def prepare_output_table(filtered_df: pd.DataFrame) -> pd.DataFrame:
    """
    Format final table for Google Sheets export.

    Selects only the key metrics columns and optionally adds a 'Vault' name column.
    """
    formatted = format_lifetime_table(filtered_df.head(50)).copy()

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

def upload_to_google_sheet(df: pd.DataFrame, service_account_file: Path, sheet_url: str, worksheet_name: str) -> None:
    """
    Upload result DataFrame to Google Sheet using a service account.

    Clears existing rows before upload. Requires:
    - gspread
    - google-auth
    """
    if gspread is None:
        raise RuntimeError("Missing gspread or google-auth libraries") from _IMPORT_ERROR

    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(str(service_account_file), scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(worksheet_name)

    last_row = len(sheet.get_all_values())
    if last_row > 1:
        sheet.batch_clear([f"A2:Z{last_row}"])  # Keep header, clear all below

    set_with_dataframe(sheet, df, row=2, col=1, include_column_header=False)
    print(f"✅ Uploaded {len(df)} rows to Google Sheet → worksheet: {worksheet_name}")

# -------------------------
# Main entrypoint
# -------------------------

def main():
    """
    Main script entry point:
    - Load data from disk
    - Filter by stablecoin and chain ID
    - Compute returns and lifetime metrics
    - Prepare final result table
    - Upload to Google Sheets (unless --skip-upload)
    """
    args = parse_args()
    chain_id = get_chain_id_from_env()
    sheet_url = get_env_or_default("SHEET_URL", args.sheet_url)
    worksheet_name = get_worksheet_name(chain_id)
    service_account_file = args.service_account_file

    prices_df, vault_db = load_data(args.output_folder)
    summarise_prices(prices_df, vault_db)

    prices_df, _ = filter_stablecoin_vaults(prices_df, vault_db)
    prices_df = filter_chain(prices_df, chain_id)

    returns_df = calculate_returns(prices_df)
    lifetime_df = calculate_lifetime_data(returns_df, vault_db)
    filtered_df = apply_thresholds(lifetime_df)
    final_df = prepare_output_table(filtered_df)

    print("Top 10 vaults:")
    print(final_df.head(10).to_markdown(index=False, tablefmt="github"))

    if args.skip_upload:
        print("Skipping Google Sheets upload as requested")
    else:
        upload_to_google_sheet(final_df, service_account_file, sheet_url, worksheet_name)

if __name__ == "__main__":
    main()
