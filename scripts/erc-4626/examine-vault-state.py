"""Examine a scan state for a single vault.

Moved to: `erc-4626-examine-vault-reader-state.ipynb`

"""

import logging
import os
import pickle
from pathlib import Path
from pprint import pprint

import pandas as pd

from IPython.display import display

from eth_defi.research.wrangle_vault_prices import assign_unique_names
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import read_default_vault_prices, DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, VaultDatabase

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e


logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    frequency = "1h"
    output_folder = Path("~/.tradingstrategy/vaults").expanduser()
    reader_state_db = output_folder / f"vault-reader-state-{frequency}.pickle"

    vault_id = os.environ.get("VAULT_ID")
    assert vault_id is not None, "Set VAULT_ID environment variable"
    spec = VaultSpec.parse_string(vault_id)

    reader_states: dict[VaultSpec, dict] = pickle.load(reader_state_db.open("rb"))

    if not spec in reader_states:
        raise ValueError(f"Vault {spec} not found in reader states")

    state = reader_states[spec]

    print(f"Vault {spec} state:")
    for key, value in state.items():
        print(f"  {key}: {value}")

    # Check price data
    print(f"Checking cleaned price data {DEFAULT_RAW_PRICE_DATABASE}")
    prices_df = read_default_vault_prices()
    cleaned_df = vault_prices_df = prices_df.loc[prices_df["id"] == vault_id]

    data = {
        "First timestamp": vault_prices_df.index.min(),
        "Last timestamp": vault_prices_df.index.max(),
        "Last block": f"{vault_prices_df['block_number'].iloc[-1]:,}",
        "First price": vault_prices_df["share_price"].iloc[0],
        "Last price": vault_prices_df["share_price"].iloc[-1],
        "Last price (raw)": vault_prices_df["raw_share_price"].iloc[-1],
        "Last TVL": vault_prices_df["total_assets"].iloc[-1],
        "Rows (vault)": len(vault_prices_df),
        "Rows (all)": f"{len(prices_df):,}",
        "Last timestamp (all)": prices_df.index.max(),
    }
    pprint(data)

    # Check raw price data
    vault_db = VaultDatabase.read()
    print(f"Checking uncleaned price data {DEFAULT_UNCLEANED_PRICE_DATABASE}")
    prices_df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)
    prices_df = assign_unique_names(vault_db.rows, prices_df, logger=lambda x: None)
    vault_prices_df = prices_df.loc[prices_df["id"] == vault_id]
    vault_prices_df = vault_prices_df.set_index("timestamp")

    data = {
        "First timestamp": vault_prices_df.index.min(),
        "Last timestamp": vault_prices_df.index.max(),
        "Last block": f"{vault_prices_df['block_number'].iloc[-1]:,}",
        "First raw share price": vault_prices_df["share_price"].iloc[0],
        "Last raw share price": vault_prices_df["share_price"].iloc[-1],
        "Min raw share price": vault_prices_df["share_price"].min(),
        "Max raw share price": vault_prices_df["share_price"].max(),
        "Min TVL": f"${vault_prices_df['total_assets'].min():,.0f}",
        "Max TVL": f"${vault_prices_df['total_assets'].max():,.0f}",
        "Last TVL": vault_prices_df["total_assets"].iloc[-1],
        "Rows (vault)": len(vault_prices_df),
        "Rows (all)": f"{len(prices_df):,}",
    }
    pprint(data)

    print("Last 50 rows of cleaned price data:")
    display(cleaned_df.tail(50))

    print("All ok")


if __name__ == "__main__":
    main()
