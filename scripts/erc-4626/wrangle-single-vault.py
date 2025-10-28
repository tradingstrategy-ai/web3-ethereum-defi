"""See our data cleaning functions fork with particularly nasty vaults"""

import pandas as pd
from IPython.display import display

from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, DEFAULT_RAW_PRICE_DATABASE
from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data


#: Untangled Finance
VAULT = "42161-0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9"


def main():
    print(f"Starting to clean vault prices data for vault: {VAULT}")
    setup_console_logging()

    vault_db: VaultDatabase = VaultDatabase.read()
    prices_df = pd.read_parquet(DEFAULT_RAW_PRICE_DATABASE)

    spec = VaultSpec.parse_string(VAULT)
    vault_db = vault_db.limit_to_single_vault(spec)
    prices_df = prices_df.loc[(prices_df["chain"] == spec.chain_id) & (prices_df["address"] == spec.vault_address)]

    print(f"We have {len(vault_db)} metadata entries and {len(prices_df)} price rows after filtering")

    enhanced_prices_df = process_raw_vault_scan_data(
        vault_db,
        prices_df,
        logger=print,
        display=display,
    )

    enhanced_prices_df = enhanced_prices_df.sort_values(by=["id", "timestamp"])

    min_price = enhanced_prices_df["share_price"].min()
    max_price = enhanced_prices_df["share_price"].max()

    min_returns = enhanced_prices_df["returns_1h"].min()
    max_returns = enhanced_prices_df["returns_1h"].max()

    print(f"Share price range: {min_price} - {max_price} / USD")
    print(f"1h returns range: {min_returns * 100} - {max_returns * 100} %")


if __name__ == "__main__":
    main()
