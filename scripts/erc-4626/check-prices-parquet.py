"""Check Parquet vault share prices file."""

import pandas as pd

from eth_defi.chain import get_chain_name
from eth_defi.vault.vaultdb import read_default_vault_prices, DEFAULT_UNCLEANED_PRICE_DATABASE


def main():

    print(f"Reading vault prices from {DEFAULT_UNCLEANED_PRICE_DATABASE}, file size is {DEFAULT_UNCLEANED_PRICE_DATABASE.stat().st_size / 1_000_000:.2f} MB")
    df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)

    # Get ethereum
    # df = df[df.chain == 1]

    print(f"We have {len(df):,} price rows for Ethereum chain")

    # Count by vault
    address_counts = df["address"].value_counts()

    # Or for more detail with percentages:
    print(f"\nTop rows by address:")
    for address, count in address_counts.head(100).items():
        print(f"{address}: {count:,}")

    # Count by chain
    chain_couns = df["chain"].value_counts()

    # Or for more detail with percentages:
    print(f"\nTop rows by chain:")
    for chain_id, count in chain_couns.head(100).items():
        chain_name = get_chain_name(chain_id)
        print(f"{chain_name} ({chain_id}): {count:,}")




if __name__ == "__main__":
    main()
