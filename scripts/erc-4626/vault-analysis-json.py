#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Multi-chain vault analysis + safe JSON export.

Features:
- Performs lifetime metric analysis for all available chains.
- Filters and formats results for the top-performing vaults.
- Safely exports to JSON with NaN/Inf -> null sanitization.
- Normalizes column keys into snake_case.
- Uses column-wise .map(parse_value) to comply with modern pandas.
- Uses allow_nan=False to guarantee strict JSON validity.

To test out:

.. code-block:: shell

    OUTPUT_JSON=/tmp/top-vaults.json python scripts/erc-4626/vault-analysis-json.py

"""

import os
import re
import json
import math
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

# Import core TradingStrategy / eth_defi modules
from eth_defi.vault.base import VaultSpec  # noqa: F401
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.chain import get_chain_name
from eth_defi.research.vault_metrics import (
    calculate_lifetime_metrics,
    clean_lifetime_metrics,
    format_lifetime_table,
    export_lifetime_row,
)

# --------------------------------------------------------------------
# Configuration via environment variables
# --------------------------------------------------------------------
MONTHS = int(os.getenv("MONTHS", "3"))  # Time window in months
EVENT_THRESHOLD = int(os.getenv("EVENT_THRESHOLD", "5"))  # Min event count
MAX_ANNUALISED_RETURN = float(os.getenv("MAX_ANNUALISED_RETURN", "0.5"))  # Cap annualized return at 50%
MIN_TVL = float(os.getenv("MIN_TVL", "50000"))  # Minimum TVL filter
TOP_PER_CHAIN = int(os.getenv("TOP_PER_CHAIN", "30"))  # Top N vaults per chain
OUTPUT_JSON = os.getenv("OUTPUT_JSON", "/root/top_vaults_analysis.json")
DATA_DIR = Path(os.getenv("DATA_DIR", "~/.tradingstrategy/vaults")).expanduser()
PARQUET_FILE = DATA_DIR / "cleaned-vault-prices-1h.parquet"


# --------------------------------------------------------------------
# Helper functions for JSON export
# --------------------------------------------------------------------
def normalize_key(col_name: str) -> str:
    """Convert column headers to normalized snake_case keys."""
    col_name = col_name.strip().lower()
    col_name = re.sub(r"[^a-z0-9]+", "_", col_name)  # Replace spaces and special chars with underscores
    col_name = re.sub(r"_+", "_", col_name)  # Collapse multiple underscores
    return col_name.strip("_")


def parse_value(val):
    """Safely convert cell values to JSON-compliant Python types."""
    # Handle None / NaN / pandas NA
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if pd.isna(val):
        return None

    # Convert pandas.Timestamp or datetime to ISO string
    if isinstance(val, (pd.Timestamp, datetime)):
        return val.isoformat()

    # Handle numeric types (int/float/numpy)
    if isinstance(val, (int, float, np.integer, np.floating)):
        try:
            f = float(val)
        except Exception:
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        # Cast integers back to int when possible
        if isinstance(val, (int, np.integer)) or (f.is_integer() and not math.isinf(f)):
            try:
                return int(f)
            except Exception:
                return f
        return f

    # Handle strings (including %, commas, and "unknown")
    if isinstance(val, str):
        v = val.strip()
        if v == "" or v.lower() == "unknown":
            return None
        # Convert percent strings like "12%" -> 0.12
        if v.endswith("%"):
            try:
                return float(v[:-1]) / 100.0
            except Exception:
                return None
        # Remove thousand separators like "1,234"
        if "," in v:
            try:
                return float(v.replace(",", ""))
            except Exception:
                pass
        # Try to convert numeric string to float
        try:
            return float(v)
        except Exception:
            return v

    # Fallback: return the value as-is
    return val


def sanitize(o):
    """
    Recursively replace NaN/Inf with None for any nested dict/list/scalar.
    Ensures json.dump(..., allow_nan=False) will not raise ValueError.
    """
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, dict):
        return {k: sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [sanitize(v) for v in o]
    return o


def find_non_serializable_paths(obj, path=None, results=None):
    """
    Recursively traverses a Python object (dict or list) and collects paths to non-serializable values or invalid keys.

    Args:
        obj: The object to check (dict, list, or nested combination).
        path: Current path (list of keys/indices; internal use).
        results: List to collect issues (internal use).

    Returns:
        List of tuples: (path_list, issue_description) for each problem found.
        Empty list if everything is serializable.
    """
    if path is None:
        path = []
    if results is None:
        results = []

    # Valid primitive types
    if isinstance(obj, (str, int, float, bool, type(None))):
        return results

    # Handle lists: recurse on each element
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = path + [i]
            find_non_serializable_paths(item, new_path, results)

    # Handle dicts: check keys are strings, then recurse on values
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                results.append((path + [key], f"Non-string key: {type(key).__name__}"))
            new_path = path + [key]
            find_non_serializable_paths(value, new_path, results)

    # Anything else is non-serializable
    else:
        results.append((path, f"Non-serializable type: {type(obj).__name__}"))

    return results


# --------------------------------------------------------------------
# Step 2: Load database and parquet price data
# --------------------------------------------------------------------
data_folder = DATA_DIR
vault_db = VaultDatabase.read()
cleaned_data_parquet_file = PARQUET_FILE
prices_df = pd.read_parquet(cleaned_data_parquet_file)

print(f"We have {len(vault_db):,} vaults in the database and {len(prices_df):,} price rows.")

# --------------------------------------------------------------------
# Step 3: Filter data for the last N months
# --------------------------------------------------------------------
last_sample_at = prices_df.index[-1]  # Latest timestamp
three_months_ago = last_sample_at - pd.DateOffset(months=MONTHS)
PERIOD = [three_months_ago, last_sample_at]

mask = (prices_df.index >= PERIOD[0]) & (prices_df.index <= PERIOD[1])
prices_df = prices_df[mask]
print(f"✅ Trimmed to {len(prices_df):,} rows from {PERIOD[0]} to {PERIOD[1]}")

# --------------------------------------------------------------------
# Step 4: Examine per-chain data availability
# --------------------------------------------------------------------
chain_ids = sorted(prices_df["chain"].unique())
for chain_id in chain_ids:
    chain_name = get_chain_name(chain_id)
    print(f"\n🔍 Examining chain {chain_name} ({chain_id})")
    chain_prices_df = prices_df[(prices_df["chain"] == chain_id) & (prices_df.index >= PERIOD[0]) & (prices_df.index <= PERIOD[1])]
    print(f"📈 Rows: {len(chain_prices_df):,} for chain {chain_name}")
    if not chain_prices_df.empty:
        print(chain_prices_df.head(1))
    else:
        print("⚠️ No data available for this chain in selected period.")

# --------------------------------------------------------------------
# Step 5: Tally vault and price counts per chain
# --------------------------------------------------------------------
for selected_chain_id in chain_ids:
    chain_name = get_chain_name(selected_chain_id)
    vault_db_filtered = {spec: vault for spec, vault in vault_db.items() if spec.chain_id == selected_chain_id}
    vault_df = prices_df[prices_df["chain"] == selected_chain_id]
    print(f"Chain {chain_name}: {len(vault_db_filtered):,} vaults, {len(vault_df):,} price rows.")

# --------------------------------------------------------------------
# Step 6: Calculate and clean lifetime metrics per chain
# --------------------------------------------------------------------
combined_lifetime_dfs = []

for selected_chain_id in chain_ids:
    chain_name = get_chain_name(selected_chain_id)
    print(f"\n📊 Calculating lifetime metrics for {chain_name} ({selected_chain_id})")

    # Filter vaults and prices for this chain
    vault_db_filtered = {spec: vault for spec, vault in vault_db.items() if spec.chain_id == selected_chain_id}
    vault_df = prices_df[prices_df["chain"] == selected_chain_id]

    if not vault_db_filtered or vault_df.empty:
        print("⚠️ No vaults or price data found for this chain. Skipping...")
        continue

    # Compute raw metrics
    lifetime_data_df = calculate_lifetime_metrics(vault_df, vault_db_filtered)

    # Clean and cap unrealistic metrics
    print(f"🧹 Cleaning metrics for {len(lifetime_data_df):,} vaults on {chain_name}")
    lifetime_data_df = clean_lifetime_metrics(
        lifetime_data_df,
        max_annualised_return=MAX_ANNUALISED_RETURN,
    )

    # Filter out vaults with too few events
    original_count = len(lifetime_data_df)
    lifetime_data_df = lifetime_data_df[lifetime_data_df["event_count"] >= EVENT_THRESHOLD]
    print(f"✅ Filtered event count >= {EVENT_THRESHOLD}: {len(lifetime_data_df):,} vaults (removed {original_count - len(lifetime_data_df):,})")

    # Tag with chain ID and append to combined list
    combined_lifetime_dfs.append(lifetime_data_df)

# Combine results from all chains
if combined_lifetime_dfs:
    all_lifetime_df = pd.concat(combined_lifetime_dfs)
    all_lifetime_df = all_lifetime_df.sort_values("one_month_cagr", ascending=False)
    print(f"\n✅ Final metrics table for all chains: {len(all_lifetime_df):,} vaults total")
    print(all_lifetime_df.head(5))
else:
    print("❌ No metrics were calculated. Check input data.")
    all_lifetime_df = pd.DataFrame()

# --------------------------------------------------------------------
# Step 7: Filter by TVL and format output table
# --------------------------------------------------------------------
if not all_lifetime_df.empty:
    min_tvl = MIN_TVL
    filtered_df = all_lifetime_df[all_lifetime_df["current_nav"] >= min_tvl]
    print(f"\n✅ Vaults filtered by min TVL ${int(min_tvl):,}: {len(filtered_df):,} remaining.")

    # Select top N vaults per chain by 1M CAGR
    top_vaults_per_chain = filtered_df.sort_values("one_month_cagr", ascending=False).groupby("chain", group_keys=False).head(TOP_PER_CHAIN)

    # Format output table for readability
    formatted_df = format_lifetime_table(
        top_vaults_per_chain,
        add_index=True,
        add_address=True,
    )

    # Log short summary for each chain
    for chain_id_display in formatted_df["Chain"].unique():
        chain_df = formatted_df[formatted_df["Chain"] == chain_id_display]
        print(f"\n🔗 Chain {chain_id_display}: Top {len(chain_df)} vaults")
        print(", ".join(chain_df.head(5)["Name"]))
else:
    print("Skipping TVL filtering as no metrics were calculated.")
    formatted_df = pd.DataFrame()

# --------------------------------------------------------------------
## --- Cell 8: Safe export to JSON (sorted by chain + 1M return ann.) ---
if not formatted_df.empty:
    SORT_COLUMN = os.getenv("SORT_COLUMN", "1M return ann.")  # Default sort column
    sort_col_norm = normalize_key(SORT_COLUMN)  # Normalize to match column names, e.g. "1M return ann." -> "1m_return_ann"

    # 1️⃣ Replace ±Inf -> NA, then NA -> None
    df = top_vaults_per_chain.copy()

    # 5️⃣ Convert DataFrame → list of dicts
    # vaults = df.to_dict(orient="records")
    vaults = [export_lifetime_row(r) for _, r in df.iterrows()]

    # 6️⃣ Add metadata and deep sanitize
    output_data = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vaults": vaults,
    }

    results = find_non_serializable_paths(output_data)
    if results:
        print("❌ Found non-serializable values in output data:")
        for path, issue in results:
            path_str = " -> ".join(str(p) for p in path)
            print(f" - Path: {path_str}: {issue}")
        raise ValueError("Non-serializable values found; aborting JSON export.")

    # 7️⃣ Write to JSON file (strict mode)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, allow_nan=False)

    print(f"✅ Exported {len(vaults):,} to {OUTPUT_JSON}")
else:
    print("Skipping JSON export as there is no formatted data.")
