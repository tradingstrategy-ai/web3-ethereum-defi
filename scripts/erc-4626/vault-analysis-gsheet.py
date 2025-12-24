#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Arbitrum analysis script (converted from your notebook) + Google Sheets upload.

- Keeps the original analysis logic.
- Removes notebook-only display().
- Adds optional Google Sheets upload using a Service Account JSON.
- All comments are in English.

Environment variables (optional):
  SELECTED_CHAIN_ID=42161
  MONTHS=3
  MIN_TVL=25000
  DATA_DIR=~/.tradingstrategy/vaults
  PARQUET_FILE=~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet
  MAX_ANNUALISED_RETURN=0.5

Google Sheets (optional):
  GS_SERVICE_ACCOUNT_FILE=/path/to/service_account.json
  GS_SHEET_URL=https://docs.google.com/spreadsheets/d/<ID>/edit
  GS_WORKSHEET_NAME=TopVaults
"""

import os
from pathlib import Path
import pandas as pd

# Keep imports consistent with the notebook (even if not all are used directly)
from eth_defi.vault.base import VaultSpec  # noqa: F401
from eth_defi.research.notebook import set_large_plotly_chart_font  # noqa: F401
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.chain import get_chain_name
from eth_defi.research.vault_metrics import (
    calculate_lifetime_metrics,
    clean_lifetime_metrics,
    format_lifetime_table,
)

# -----------------------------
# Optional environment settings
# -----------------------------
SELECTED_CHAIN_ID = int(os.getenv("SELECTED_CHAIN_ID", "42161"))  # Arbitrum
MONTHS = int(os.getenv("MONTHS", "3"))
MIN_TVL = float(os.getenv("MIN_TVL", "25000"))
DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~/.tradingstrategy/vaults"))
PARQUET_FILE = os.getenv("PARQUET_FILE", os.path.join(DATA_DIR, "cleaned-vault-prices-1h.parquet"))
MAX_ANNUALISED_RETURN = float(os.getenv("MAX_ANNUALISED_RETURN", "0.5"))

# -----------------------------
# Google Sheets configuration
# -----------------------------
GS_SERVICE_ACCOUNT_FILE = os.getenv("GS_SERVICE_ACCOUNT_FILE")  # path to service account JSON
GS_SHEET_URL = os.getenv("GS_SHEET_URL")  # spreadsheet URL
GS_WORKSHEET_NAME = os.getenv("GS_WORKSHEET_NAME", "TopVaults")


# -----------------------------
# Simple Google Sheets uploader
# -----------------------------
def upload_to_google_sheet(
    dataframe: pd.DataFrame,
    service_account_file: Path,
    sheet_url: str,
    worksheet_name: str = "Sheet1",
) -> None:
    """
    Upload a DataFrame to a Google Sheet using a Service Account.

    Behavior:
      - Open spreadsheet by URL.
      - Create worksheet if missing.
      - Clear worksheet content.
      - Write DataFrame (with column headers), auto-resizing the sheet.

    Requirements:
      - Share the target Google Sheet with the Service Account email (Editor).
      - pip install gspread gspread-dataframe
    """
    import gspread
    from gspread_dataframe import set_with_dataframe

    gc = gspread.service_account(filename=str(service_account_file))
    sh = gc.open_by_url(sheet_url)

    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="26")

    ws.clear()

    set_with_dataframe(
        ws,
        dataframe,
        include_index=False,
        include_column_header=True,
        resize=True,
    )

    print(f"✅ Uploaded {len(dataframe)} rows to worksheet '{worksheet_name}'.")


def main():
    # Pandas display options (kept from notebook for consistent formatting in prints)
    pd.options.display.float_format = "{:,.2f}".format
    pd.options.display.max_columns = None
    pd.options.display.max_rows = None

    # -----------------------------
    # Load database and parquet data
    # -----------------------------
    data_folder = Path(DATA_DIR).expanduser()
    vault_db = VaultDatabase.read()

    cleaned_data_parquet_file = Path(PARQUET_FILE)
    prices_df = pd.read_parquet(cleaned_data_parquet_file)

    print(f"We have {len(vault_db):,} vaults in the database and {len(prices_df):,} price rows.")

    # -----------------------------
    # Select chain and trim period
    # -----------------------------
    selected_chain_id = SELECTED_CHAIN_ID
    chain_name = get_chain_name(selected_chain_id)
    print(f"Examining chain {chain_name} ({selected_chain_id})")

    last_sample_at = prices_df.index[-1]
    three_months_ago = last_sample_at - pd.DateOffset(months=MONTHS)

    PERIOD = [three_months_ago, last_sample_at]

    mask = (prices_df.index >= PERIOD[0]) & (prices_df.index <= PERIOD[1])
    prices_df = prices_df[mask]
    prices_df = prices_df[prices_df["chain"] == selected_chain_id]
    print(f"Trimmed period contains {len(prices_df):,} price rows across all vaults on {chain_name}.")

    # Brief preview to console (no display())
    print(prices_df.head(4).to_string())

    # -----------------------------
    # Filter vaults to the chain
    # -----------------------------
    vault_db = {spec: vault for spec, vault in vault_db.items() if spec.chain_id == selected_chain_id}
    vault_df = prices_df[prices_df["chain"] == selected_chain_id]
    print(f"We have total of {len(vault_db):,} vaults on chain {chain_name}, with {len(vault_df):,} rows.")

    # -----------------------------
    # Calculate and clean metrics
    # -----------------------------
    lifetime_data_df = calculate_lifetime_metrics(prices_df, vault_db)

    print(f"Cleaning metrics for {len(lifetime_data_df):,} vaults")
    lifetime_data_df = clean_lifetime_metrics(
        lifetime_data_df,
        max_annualised_return=MAX_ANNUALISED_RETURN,  # 50% max return
    )
    print(f"Calculated lifetime metrics for {len(lifetime_data_df):,} vaults")

    lifetime_data_df = lifetime_data_df.sort_values(["one_month_cagr"], ascending=False)

    print("\nTop-2 lifetime metrics preview (no display):")
    print(lifetime_data_df.head(2).to_string())

    # -----------------------------
    # Filter by TVL and format table
    # -----------------------------
    min_tvl = MIN_TVL
    lifetime_data_filtered_df = lifetime_data_df[lifetime_data_df["current_nav"] >= min_tvl]
    print(f"\nVaults filtered by min TVL of ${min_tvl:,.0f}, remaining {len(lifetime_data_filtered_df):,} vaults.")

    formatted_df = format_lifetime_table(
        lifetime_data_filtered_df.head(50),
        add_index=True,
        add_address=True,
    )

    # Console summary
    max_address_dump = 12
    top_names = ", ".join(formatted_df.head(max_address_dump)["Name"])
    top_addrs = ", ".join(formatted_df.head(max_address_dump)["Address"])
    print(f"Top {max_address_dump} vaults by 1M annualised return (names): {top_names}")
    print(f"Top {max_address_dump} vaults by 1M annualised return (addresses): {top_addrs}")

    # -----------------------------
    # Optional: upload to Google Sheets
    # -----------------------------
    if GS_SERVICE_ACCOUNT_FILE and GS_SHEET_URL:
        try:
            upload_to_google_sheet(
                dataframe=formatted_df,
                service_account_file=Path(GS_SERVICE_ACCOUNT_FILE),
                sheet_url=GS_SHEET_URL,
                worksheet_name=GS_WORKSHEET_NAME,
            )
        except Exception as e:
            print(f"⚠️ Google Sheets upload failed: {e}")
    else:
        print("ℹ️ Google Sheets upload skipped (set GS_SERVICE_ACCOUNT_FILE and GS_SHEET_URL to enable).")


if __name__ == "__main__":
    main()
