"""Check Parquet vault share prices file."""

import pandas as pd

from eth_defi.vault.vaultdb import read_default_vault_prices, DEFAULT_UNCLEANED_PRICE_DATABASE


def main():
    df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)

    # Get ethereum
    df = df[df.chain == 1]

    print(f"We have {len(df):,} price rows for Ethereum chain")

    # Count by vault
    address_counts = df["address"].value_counts()

    # Or for more detail with percentages:
    print(f"\nTop rows by address:")
    for address, count in address_counts.head(100).items():
        print(f"{address}: {count}")
    import ipdb

    ipdb.set_trace()


if __name__ == "__main__":
    main()
