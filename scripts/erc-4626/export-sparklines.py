"""Export all sparklines to Cloudflare R2.

- Run after cleaned prices 1h is generated

"""

import os

import pandas as pd

from eth_defi.vault.base import VaultSpec
from eth_defi.research.sparkline import export_sparkline_as_png, extract_vault_price_data
from eth_defi.vault.vaultdb import VaultDatabase, read_default_vault_prices, VaultRow
from eth_defi.research.sparkline import upload_to_r2


def is_vault_included(
    lead: Vault
):
    detection_data = row



def main():

    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    account_id = os.environ.get("R2_SPARKLINE_ACCOUNT_ID")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")

    assert bucket_name, "R2_SPARKLINE_BUCKET_NAME environment variable is required"

    vault_db = VaultDatabase.read()
    prices_df = read_default_vault_prices()

    # Select entries with peak TVL 50k USD

    vault_rows = [r for r in vault_db.rows.values() if is_vault_included(r)]

    #

    # Export last 90 days
    prices_df = prices_df[prices_df["timestamp"] >= (prices_df["timestamp"].max() - pd.Timedelta(days=90))]
    prices_df = prices_df.set_index(["id", "timestamp"]).sort_index().reset_index()

    for row in vault_rows:
        spec = VaultSpec(
            chain_id=row["ChainID"],
            vault_address=row["Address"],
        )
        vault_id = spec.as_string_id()

        vault_prices_df = prices_df[vault_id]

        vault_prices_df = extract_vault_price_data(
            spec=spec,
            prices_df=prices_df,
        )

        vault_prices_df = vault_prices_df.set_index("timestamp")

        png_bytes = export_sparkline_as_png(
            vault_prices_df,
            width=512,
            height=128,
        )

        object_name = f"sparkline-90d-{spec.as_string_id()}.png"
        upload_to_r2(
            payload=png_bytes,
            bucket_name=bucket_name,
            object_name=object_name,
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            content_type="image/png",
        )
        print(f"Uploaded sparkline to R2 bucket '{bucket_name}' as '{object_name}'")


if __name__ == "__main__":
    main()
