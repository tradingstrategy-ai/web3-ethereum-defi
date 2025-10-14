import os  # <-- thêm
import pandas as pd
import pickle
from pathlib import Path
import json
import re
from datetime import datetime
import numpy as np

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.chain import get_chain_name
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, clean_lifetime_metrics, format_lifetime_table


# --- Config  ENV  ---
MONTHS = int(os.getenv("MONTHS", "3"))
EVENT_THRESHOLD = int(os.getenv("EVENT_THRESHOLD", "5"))
MAX_ANNUALISED_RETURN = float(os.getenv("MAX_ANNUALISED_RETURN", "0.5"))  # 50% yearly cap
MIN_TVL = float(os.getenv("MIN_TVL", "50000"))
TOP_PER_CHAIN = int(os.getenv("TOP_PER_CHAIN", "30"))
OUTPUT_JSON = os.getenv("OUTPUT_JSON", "vaults_raw_normalized.json")
DATA_DIR = Path(os.getenv("DATA_DIR", "~/.tradingstrategy/vaults")).expanduser()
PARQUET_FILE = DATA_DIR / "cleaned-vault-prices-1h.parquet"


# --- Cell 2: Load data ---
data_folder = DATA_DIR  # Path("~/.tradingstrategy/vaults").expanduser()

vault_db = VaultDatabase.read()

cleaned_data_parquet_file = PARQUET_FILE  # data_folder / "cleaned-vault-prices-1h.parquet"
prices_df = pd.read_parquet(cleaned_data_parquet_file)

print(f"We have {len(vault_db):,} vaults in the database and {len(prices_df):,} price rows.")


# --- Cell 3: Filter data for the last 3 months ---
# Step 3: Trim data for the last 3 months, WITHOUT filtering by chain
last_sample_at = prices_df.index[-1]  # Latest timestamp
three_months_ago = last_sample_at - pd.DateOffset(months=MONTHS)

PERIOD = [three_months_ago, last_sample_at]

# Filter data by time range
mask = (prices_df.index >= PERIOD[0]) & (prices_df.index <= PERIOD[1])
prices_df = prices_df[mask]

print(f"✅ Trimmed to {len(prices_df):,} rows from {PERIOD[0]} to {PERIOD[1]}")


# --- Cell 4: Examine data per chain ---
# ✅ Get the last 3 months' time frame
last_sample_at = prices_df.index[-1]
three_months_ago = last_sample_at - pd.DateOffset(months=MONTHS)
PERIOD = [three_months_ago, last_sample_at]

# ✅ Get a list of all chain_ids from the price data
chain_ids = sorted(prices_df["chain"].unique())

# ✅ Iterate over each chain_id
for chain_id in chain_ids:
    chain_name = get_chain_name(chain_id)
    print(f"\n🔍 Examining chain {chain_name} ({chain_id})")

    # ✅ Filter price data by time and chain_id
    chain_prices_df = prices_df[(prices_df["chain"] == chain_id) & (prices_df.index >= PERIOD[0]) & (prices_df.index <= PERIOD[1])]

    print(f"✅ Trimmed period: {PERIOD[0]} → {PERIOD[1]}")
    print(f"📈 Trimmed price rows: {len(chain_prices_df):,} for chain {chain_name}")

    # ✅ Display the first few rows for verification
    if not chain_prices_df.empty:
        print(chain_prices_df.head(1))
    else:
        print("⚠️ No data available for this chain in selected period.")


# --- Cell 5: Tally vault and price counts per chain ---
# ✅ Get a list of all chain_ids from the price data
chain_ids = sorted(prices_df["chain"].unique())

# ✅ Iterate over each chain_id and print the corresponding vault + price results
for selected_chain_id in chain_ids:
    chain_name = get_chain_name(selected_chain_id)

    # ✅ Keep the original logic
    vault_db_filtered = {spec: vault for spec, vault in vault_db.items() if spec.chain_id == selected_chain_id}

    vault_df = prices_df[prices_df["chain"] == selected_chain_id]

    print(f"We have total of {len(vault_db_filtered):,} vaults on chain {chain_name}, with {len(vault_df):,} rows.")


# --- Cell 6: Calculate and clean metrics ---
combined_lifetime_dfs = []

# Get chain_ids from the price data
chain_ids = sorted(prices_df["chain"].unique())

for selected_chain_id in chain_ids:
    chain_name = get_chain_name(selected_chain_id)
    print(f"\n📊 Calculating lifetime metrics for {chain_name} ({selected_chain_id})")

    # ✅ Filter vaults and prices for the selected chain
    vault_db_filtered = {spec: vault for spec, vault in vault_db.items() if spec.chain_id == selected_chain_id}

    vault_df = prices_df[prices_df["chain"] == selected_chain_id]

    if not vault_db_filtered or vault_df.empty:
        print("⚠️ No vaults or price data found for this chain. Skipping...")
        continue

    # ✅ Calculate raw lifetime metrics
    lifetime_data_df = calculate_lifetime_metrics(
        vault_df,
        vault_db_filtered,
    )

    print(f"🧹 Cleaning metrics for {len(lifetime_data_df):,} vaults on {chain_name}")
    lifetime_data_df = clean_lifetime_metrics(
        lifetime_data_df,
        max_annualised_return=MAX_ANNUALISED_RETURN,
    )

    # ✅ Apply custom filter for event count >= EVENT_THRESHOLD
    original_count = len(lifetime_data_df)
    lifetime_data_df = lifetime_data_df[lifetime_data_df["event_count"] >= EVENT_THRESHOLD]
    print(f"✅ Filtered event count >= {EVENT_THRESHOLD}: {len(lifetime_data_df):,} vaults (removed {original_count - len(lifetime_data_df):,})")

    # ✅ Add chain info for clarity
    lifetime_data_df["chain"] = selected_chain_id
    combined_lifetime_dfs.append(lifetime_data_df)

# ✅ Combine all chains together
if combined_lifetime_dfs:
    all_lifetime_df = pd.concat(combined_lifetime_dfs)
    all_lifetime_df = all_lifetime_df.sort_values("one_month_cagr", ascending=False)

    print(f"\n✅ Final metrics table for all chains: {len(all_lifetime_df):,} vaults total")
    print(all_lifetime_df.head(5))
else:
    print("❌ No metrics were calculated. Check input data.")
    all_lifetime_df = pd.DataFrame()  # Initialize an empty DataFrame to avoid errors in subsequent steps


# --- Cell 7: Filter by TVL and format the results table ---
if not all_lifetime_df.empty:
    # ✅ Set TVL threshold
    min_tvl = MIN_TVL

    # ✅ Filter vaults by current NAV (TVL)
    filtered_df = all_lifetime_df[all_lifetime_df["current_nav"] >= min_tvl]

    print(f"\n✅ Vaults filtered by min TVL of ${int(min_tvl):,}: {len(filtered_df):,} vaults remaining.")

    # ✅ Group by chain and get top N vaults per chain by 1M annualised return
    top_vaults_per_chain = filtered_df.sort_values("one_month_cagr", ascending=False).groupby("chain", group_keys=False).head(TOP_PER_CHAIN)

    # ✅ Format the filtered DataFrame for display
    formatted_df = format_lifetime_table(
        top_vaults_per_chain,
        add_index=True,
        add_address=True,
    )

    # ✅ Print summary
    for chain_id_display in formatted_df["Chain"].unique():
        chain_df = formatted_df[formatted_df["Chain"] == chain_id_display]
        print(f"\n🔗 Chain {chain_id_display}: Top {len(chain_df)} vaults")
        print(", ".join(chain_df.head(5)["Name"]))  # Optional: show 5 vault names
else:
    print("Skipping TVL filtering as no metrics were calculated.")
    formatted_df = pd.DataFrame()  # Initialize an empty DataFrame


# --- Cell 8: Export data to JSON file ---
if not formatted_df.empty:

    def normalize_key(col_name: str) -> str:
        """Convert column names like 'Lifetime return ann.' → 'lifetime_return_ann'"""
        col_name = col_name.strip().lower()
        col_name = re.sub(r"[^a-z0-9]+", "_", col_name)  # Replace spaces, %, ., /, etc.
        col_name = re.sub(r"_+", "_", col_name)  # Collapse multiple underscores
        return col_name.strip("_")

    def parse_value(val):
        """Convert %, commas, unknown → proper numeric/null types; Timestamp → str."""
        if isinstance(val, (pd.Timestamp, datetime)):
            return val.isoformat()
        if isinstance(val, (np.int64, np.float64)):
            return float(val)
        if isinstance(val, str):
            v = val.strip()
            if v.lower() == "unknown" or v == "":
                return None
            if "%" in v:
                try:
                    return float(v.replace("%", "")) / 100
                except:
                    return None
            if "," in v:
                try:
                    return float(v.replace(",", ""))
                except:
                    pass
            try:
                return float(v)
            except:
                return v
        if pd.isna(val):
            return None
        return val

    # ✅ Convert DataFrame → JSON-like list with normalized keys
    vaults = []
    for _, row in formatted_df.iterrows():
        vault = {}
        for col in formatted_df.columns:
            new_key = normalize_key(col)
            vault[new_key] = parse_value(row[col])
        vaults.append(vault)

    # ✅ Add export timestamp
    output_data = {"generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "vaults": vaults}

    # ✅ Save to JSON file
    output_path = OUTPUT_JSON  # "vaults_raw_normalized.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"✅ Exported {len(vaults):,} vaults to {output_path}")
else:
    print("Skipping JSON export as there is no formatted data.")
