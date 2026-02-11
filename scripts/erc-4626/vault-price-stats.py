"""Print statistics about vault price data parquet files.

Reads both uncleaned and cleaned vault price data and prints
per-chain statistics including time range, vault counts, and
utilisation data coverage.

Usage:

.. code-block:: shell

    poetry run python scripts/erc-4626/vault-price-stats.py

"""

import pandas as pd
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_RAW_PRICE_DATABASE


def print_basic_stats(df: pd.DataFrame, label: str):
    """Print per-chain basic statistics for a price dataframe.

    :param df: Vault price dataframe with chain, address, timestamp columns
    :param label: Label for the table (e.g. "Uncleaned" or "Cleaned")
    """
    # timestamp may be a column or the index depending on the file
    if "timestamp" not in df.columns and df.index.name == "timestamp":
        df = df.reset_index()

    rows = []
    for chain_id, group in sorted(df.groupby("chain")):
        chain_name = get_chain_name(chain_id)
        vault_count = group["address"].nunique()
        row_count = len(group)
        first_ts = group["timestamp"].min()
        last_ts = group["timestamp"].max()
        duration = (last_ts - first_ts).days

        rows.append(
            [
                chain_name,
                chain_id,
                vault_count,
                f"{row_count:,}",
                str(first_ts.date()),
                str(last_ts.date()),
                duration,
            ]
        )

    headers = ["Chain", "Chain ID", "Vaults", "Rows", "First date", "Last date", "Days"]
    print(f"\n{label} price data")
    print(f"Total: {len(df):,} rows, {df['address'].nunique()} vaults\n")
    print(tabulate(rows, headers=headers, tablefmt="grid"))


def print_utilisation_stats(df: pd.DataFrame):
    """Print per-chain utilisation data coverage.

    :param df: Uncleaned vault price dataframe with utilisation and available_liquidity columns
    """
    if "utilisation" not in df.columns:
        print("\nNo utilisation column found in data")
        return

    rows = []
    for chain_id, group in sorted(df.groupby("chain")):
        chain_name = get_chain_name(chain_id)
        total_rows = len(group)
        total_vaults = group["address"].nunique()

        # Utilisation coverage
        util_mask = group["utilisation"].notna()
        util_rows = util_mask.sum()
        util_vaults = group.loc[util_mask, "address"].nunique()
        util_pct = (util_rows / total_rows * 100) if total_rows > 0 else 0

        # Available liquidity coverage
        liq_mask = group["available_liquidity"].notna()
        liq_rows = liq_mask.sum()
        liq_vaults = group.loc[liq_mask, "address"].nunique()
        liq_pct = (liq_rows / total_rows * 100) if total_rows > 0 else 0

        rows.append(
            [
                chain_name,
                chain_id,
                f"{util_vaults}/{total_vaults}",
                f"{util_rows:,}/{total_rows:,}",
                f"{util_pct:.1f}%",
                f"{liq_vaults}/{total_vaults}",
                f"{liq_rows:,}/{total_rows:,}",
                f"{liq_pct:.1f}%",
            ]
        )

    headers = [
        "Chain",
        "Chain ID",
        "Util vaults",
        "Util rows",
        "Util %",
        "Liq vaults",
        "Liq rows",
        "Liq %",
    ]
    print("\nUtilisation data coverage (uncleaned)")
    print(tabulate(rows, headers=headers, tablefmt="grid"))


def main():
    # Read uncleaned data
    if not DEFAULT_UNCLEANED_PRICE_DATABASE.exists():
        print(f"Uncleaned price file not found: {DEFAULT_UNCLEANED_PRICE_DATABASE}")
        return

    print(f"Reading uncleaned prices from {DEFAULT_UNCLEANED_PRICE_DATABASE}")
    print(f"File size: {DEFAULT_UNCLEANED_PRICE_DATABASE.stat().st_size / 1_000_000:.2f} MB")
    uncleaned_df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)

    print_basic_stats(uncleaned_df, "Uncleaned")
    print_utilisation_stats(uncleaned_df)

    # Read cleaned data
    if not DEFAULT_RAW_PRICE_DATABASE.exists():
        print(f"\nCleaned price file not found: {DEFAULT_RAW_PRICE_DATABASE} (skipping)")
        return

    print(f"\nReading cleaned prices from {DEFAULT_RAW_PRICE_DATABASE}")
    print(f"File size: {DEFAULT_RAW_PRICE_DATABASE.stat().st_size / 1_000_000:.2f} MB")
    try:
        cleaned_df = pd.read_parquet(DEFAULT_RAW_PRICE_DATABASE)
    except OSError as e:
        print(f"Could not read cleaned price file: {e}")
        return

    print_basic_stats(cleaned_df, "Cleaned")


if __name__ == "__main__":
    main()
