"""Check Parquet vault share prices file."""
import pandas as pd

from eth_defi.vault.vaultdb import read_default_vault_prices, DEFAULT_UNCLEANED_PRICE_DATABASE


def main():
    df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)
    import ipdb ; ipdb.set_trace()

if __name__ == "__main__":
    main()