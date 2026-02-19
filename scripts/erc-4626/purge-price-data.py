"""Purge vault share price data for a particular chain.

- Next time the scanning will start from the scratch for that chain.

Usage:

.. code-block:: shell

    CHAIN_ID=1 python scripts/erc-4626/purge-price-data.py

    CHAIN_ID=1 python scripts/erc-4626/purge-price-data.py
    CHAIN_ID=42161 python scripts/erc-4626/purge-price-data.py
    CHAIN_ID=8453 python scripts/erc-4626/purge-price-data.py
    # Mantle
    CHAIN_ID=5000 python scripts/erc-4626/purge-price-data.py
    # Monad
    CHAIN_ID=143 python scripts/erc-4626/purge-price-data.py

"""

import logging
import os
import pickle

import pandas as pd

from eth_defi.chain import get_chain_name
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import (DEFAULT_READER_STATE_DATABASE,
                                    DEFAULT_UNCLEANED_PRICE_DATABASE,
                                    DEFAULT_VAULT_DATABASE, VaultDatabase,
                                    VaultReaderData)

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    chain_id = os.environ.get("CHAIN_ID")
    assert chain_id is not None, "Set CHAIN_ID environment variable"

    chain_id = int(chain_id)
    chain_name = get_chain_name(chain_id)

    price_parquet_fname = DEFAULT_UNCLEANED_PRICE_DATABASE
    prices_df = pd.read_parquet(price_parquet_fname)
    reader_states: VaultReaderData = pickle.load(DEFAULT_READER_STATE_DATABASE.open("rb"))
    vault_db = VaultDatabase.read()

    # Replace reader states
    new_reader_states = {spec: state for spec, state in reader_states.items() if spec.chain_id != chain_id}
    new_prices_df = prices_df[prices_df["chain"] != chain_id]

    print(f"Vault price data purge for chain {chain_id}: {chain_name}")

    print(f"Old reader states: {len(reader_states):,}")
    print(f"New reader states: {len(new_reader_states):,}")
    print(f"Reader states to be deleted: {len(reader_states) - len(new_reader_states):,}")

    print(f"Old rows: {len(prices_df):,}")
    print(f"New rows: {len(new_prices_df):,}")
    print(f"Rows to be deleted: {len(prices_df) - len(new_prices_df):,}")

    confirmation = input("Proceed: [y/N]? ")
    if confirmation.lower() != "y":
        print("Aborting")
        return

    vault_db.last_scanned_block[chain_id] = 0
    vault_db.write()
    print(f"Wrote {DEFAULT_VAULT_DATABASE}")

    new_prices_df.to_parquet(price_parquet_fname)
    print(f"Wrote {price_parquet_fname}")

    pickle.dump(new_reader_states, DEFAULT_READER_STATE_DATABASE.open("wb"))
    print(f"Wrote {DEFAULT_READER_STATE_DATABASE}")

    print("All ok")


if __name__ == "__main__":
    main()
