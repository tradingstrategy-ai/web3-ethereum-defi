"""Identify curators for all vaults and print a summary table.

Loads the vault database and cleaned price data, calculates lifetime
metrics, identifies each vault's curator, and prints a per-curator
summary sorted by total TVL.

Example:

.. code-block:: shell

    poetry run python scripts/erc-4626/identify-curators.py

Environment variables:
    - DATA_DIR: Vault data directory (default: ~/.tradingstrategy/vaults)
"""

import os
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
)
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.vaultdb import VaultDatabase

DATA_DIR = Path(os.getenv("DATA_DIR", "~/.tradingstrategy/vaults")).expanduser()
PARQUET_FILE = DATA_DIR / "cleaned-vault-prices-1h.parquet"


def main():
    # 1. Load vault database and price data
    vault_db = VaultDatabase.read()
    prices_df = pd.read_parquet(PARQUET_FILE)

    # 2. Filter to stablecoin-denominated vaults
    stablecoin_vaults = {spec: row for spec, row in vault_db.items() if is_stablecoin_like(row["Denomination"])}

    if not stablecoin_vaults:
        print("No stablecoin-denominated vaults found")
        return

    # 3. Calculate hourly returns and lifetime metrics
    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    metrics = calculate_lifetime_metrics(returns_df, stablecoin_vaults)

    # 4. Group by curator_slug and aggregate
    metrics["curator_label"] = metrics["curator_slug"].fillna("unknown")
    grouped = metrics.groupby("curator_label").agg(
        vault_count=("name", "count"),
        total_tvl=("current_nav", "sum"),
    )
    grouped = grouped.sort_values("total_tvl", ascending=False)

    # 5. Print summary table
    table_data = []
    for curator_label, row in grouped.iterrows():
        table_data.append(
            [
                curator_label,
                int(row["vault_count"]),
                f"${row['total_tvl']:,.0f}",
            ]
        )

    print(f"\nCurator summary ({len(metrics)} vaults analysed)\n")
    print(
        tabulate(
            table_data,
            headers=["Curator", "Vaults", "Total TVL"],
            tablefmt="simple",
        )
    )

    # 6. Print coverage stats
    identified = metrics["curator_slug"].notna().sum()
    total = len(metrics)
    print(f"\nCoverage: {identified}/{total} vaults identified ({identified / total * 100:.1f}%)")


if __name__ == "__main__":
    main()
