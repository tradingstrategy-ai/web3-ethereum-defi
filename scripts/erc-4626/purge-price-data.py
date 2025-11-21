"""Purge vault share price data for a particular chain.

- Next time the scanning will start from the scrath

- Pull out vault reader state from the pickled statea database and print it.
- Check both uncleaned and cleaned price data for the vault.

Usage:

.. code-block:: shell

    VAULT_ID=1-"0x00c8a649c9837523ebb406ceb17a6378ab5c74cf" python scripts/erc-4626/examine-vault-state.py

Example output:

.. code-block:: none

    Vault VaultSpec(chain_id=1, vault_address='0x00c8a649c9837523ebb406ceb17a6378ab5c74cf') state:
      last_tvl: 11747340.777844
      max_tvl: 22979214.227838
      first_seen_at_block: 23180703
      first_block: 23180899
      first_read_at: 2025-08-20 07:48:59
      last_call_at: 2025-10-21 14:01:23
      last_block: 23626399
      peaked_at: 2025-10-20 06:41:35
      faded_at: None
      entry_count: 1486
      chain_id: 1
      vault_address: 0x00C8a649C9837523ebb406Ceb17a6378Ab5C74cF
    All ok

"""

import logging
import os
import pickle
from pathlib import Path
from pprint import pprint

import pandas as pd

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
    vault_prices_df = prices_df.loc[prices_df["id"] == vault_id]

    data = {
        "First timestamp": vault_prices_df.index.min(),
        "Last timestamp": vault_prices_df.index.max(),
        "Last block": f"{vault_prices_df['block_number'].iloc[-1]:,}",
        "First price": vault_prices_df["share_price"].iloc[0],
        "Last price": vault_prices_df["share_price"].iloc[-1],
        "Last price (raw)": vault_prices_df["raw_share_price"].iloc[-1],
        "Last TVL": vault_prices_df["total_assets"].iloc[-1],
        "Price count": len(vault_prices_df),
    }
    pprint(data)

    # Check raw price data
    vault_db = VaultDatabase.read()
    print(f"Checking uncleaned price data {DEFAULT_UNCLEANED_PRICE_DATABASE}")
    prices_df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)
    prices_df = assign_unique_names(vault_db.rows, prices_df)
    vault_prices_df = prices_df.loc[prices_df["id"] == vault_id]
    vault_prices_df = vault_prices_df.set_index("timestamp")

    data = {
        "First timestamp": vault_prices_df.index.min(),
        "Last timestamp": vault_prices_df.index.max(),
        "Last block": f"{vault_prices_df['block_number'].iloc[-1]:,}",
        "First raw share price": vault_prices_df["share_price"].iloc[0],
        "Last raw share price": vault_prices_df["share_price"].iloc[-1],
        "Last TVL": vault_prices_df["total_assets"].iloc[-1],
        "Price count": len(vault_prices_df),
    }
    pprint(data)

    print("All ok")


if __name__ == "__main__":
    main()
