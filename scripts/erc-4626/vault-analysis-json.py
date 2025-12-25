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
import datetime
from pathlib import Path
from IPython.display import display

from eth_defi.token import is_stablecoin_like
# Import core TradingStrategy / eth_defi modules
from eth_defi.vault.base import VaultSpec  # noqa: F401
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.chain import get_chain_name
from eth_defi.research.vault_metrics import (
    calculate_lifetime_metrics,
    clean_lifetime_metrics,
    format_lifetime_table,
    export_lifetime_row, cross_check_data, calculate_hourly_returns_for_all_vaults,
)

# --------------------------------------------------------------------
# Configuration via environment variables
# --------------------------------------------------------------------
MONTHS = int(os.getenv("MONTHS", "3"))  # Time window in months
EVENT_THRESHOLD = int(os.getenv("EVENT_THRESHOLD", "5"))  # Min event count
MAX_ANNUALISED_RETURN = float(os.getenv("MAX_ANNUALISED_RETURN", "4.0"))  # Cap annualized return at 400%
THRESHOLD_TVL = float(os.getenv("MIN_TVL", "5000"))  # Minimum TVL filter
TOP_PER_CHAIN = int(os.getenv("TOP_PER_CHAIN", "99999"))  # Top N vaults per chain
OUTPUT_JSON = Path(os.getenv("OUTPUT_JSON", "~/.tradingstrategy/vaults/stablecoin-vault-metrics.json")).expanduser()
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


def main():
    """Main execution function for vault analysis and JSON export."""
    # --------------------------------------------------------------------
    # Step 2: Load database and parquet price data
    # --------------------------------------------------------------------
    data_folder = DATA_DIR
    vault_db = VaultDatabase.read()
    cleaned_data_parquet_file = PARQUET_FILE
    prices_df = pd.read_parquet(cleaned_data_parquet_file)
    print(f"We have {len(vault_db):,} vaults in the database and {len(prices_df):,} price rows.")

    chains = prices_df["chain"].unique()

    print(f"The report data is dated {prices_df.index.min()} - {prices_df.index.max()}")
    print(f"We have {len(prices_df):,} price rows and {len(vault_db)} vault metadata entries for {len(chains)} chains")

    # sample_vault = next(iter(vault_db.values()))
    # print("We have vault metadata keys: ", ", ".join(c for c in sample_vault.keys()))
    # display(pd.Series(sample_vault))

    print("We have prices DataFrame columns: ", ", ".join(c for c in prices_df.columns))
    print("DataFrame sample:")
    display(prices_df.head(3))

    errors = cross_check_data(
        vault_db,
        prices_df,
    )
    assert errors == 0, f"Data Cross-check found: {errors} errors"

    usd_vaults = [v for v in vault_db.values() if is_stablecoin_like(v["Denomination"])]
    print(f"The report covers {len(usd_vaults):,} stablecoin-denominated vaults out of {len(vault_db):,} total vaults")

    # Build chain-address strings for vaults we are interested in.
    # Remove Silo vaults that cause havoc after xUSD incident.
    allowed_vault_ids = (str(v["_detection_data"].chain) + "-" + v["_detection_data"].address for v in usd_vaults)

    # Filter out prices to contain only data for vaults we are interested in
    prices_df = prices_df.loc[prices_df["id"].isin(allowed_vault_ids)]
    print(f"Filtered out prices have {len(prices_df):,} rows")

    raw_returns_df = returns_df = calculate_hourly_returns_for_all_vaults(prices_df)

    lifetime_data_df = calculate_lifetime_metrics(returns_df, vault_db)

    print(f"Lifetime data has {len(lifetime_data_df):,} rows and columns: ", ", ".join(c for c in lifetime_data_df.columns))

    # Don't export all crappy vaults to keep the data more compact
    # Use peak TVL so we will export old vaults too which were popular in the past
    filtered_lifetime_data_df = lifetime_data_df[lifetime_data_df["peak_nav"] >= THRESHOLD_TVL]

    # 5️⃣ Convert DataFrame → list of dicts
    vaults = [export_lifetime_row(r) for _, r in filtered_lifetime_data_df.iterrows()]

    # 6️⃣ Add metadata and deep sanitize
    output_data = {
        "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


if __name__ == "__main__":
    main()
