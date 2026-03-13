"""Purge stale vault entries with obsolete chain IDs from the vault database.

When a synthetic chain ID is changed (e.g. Hypercore from -999 to 9999),
old entries remain in the pickle and cause slug collisions (every vault
gets a ``-2`` suffix because the same name exists under both old and new
chain IDs).

This script:

1. Detects stale chain IDs that are not in :py:data:`eth_defi.chain.CHAIN_NAMES`.
2. Shows which entries would be removed.
3. Removes them from the VaultDatabase pickle, reader states, and price
   Parquet files after user confirmation.

Usage:

.. code-block:: shell

    # Dry-run (just report)
    DRY_RUN=true poetry run python scripts/erc-4626/purge-stale-chain-ids.py

    # Actually purge
    poetry run python scripts/erc-4626/purge-stale-chain-ids.py

"""

import logging
import os
import pickle
from collections import Counter

import pandas as pd

from eth_defi.chain import CHAIN_NAMES
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import (
    DEFAULT_READER_STATE_DATABASE,
    DEFAULT_UNCLEANED_PRICE_DATABASE,
    DEFAULT_RAW_PRICE_DATABASE,
    DEFAULT_VAULT_DATABASE,
    VaultDatabase,
    VaultReaderData,
)

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes")

    vault_db = VaultDatabase.read()

    # Find chain IDs present in the database but not in CHAIN_NAMES
    chain_ids_in_db = Counter(spec.chain_id for spec in vault_db.rows.keys())
    stale_chain_ids = {cid for cid in chain_ids_in_db if cid not in CHAIN_NAMES}

    if not stale_chain_ids:
        print("No stale chain IDs found in the vault database.")
        return

    print("Stale chain IDs found in vault database:")
    for cid in sorted(stale_chain_ids):
        count = chain_ids_in_db[cid]
        # Show sample vault names
        samples = [
            v.get("Name") or "<unnamed>"
            for k, v in vault_db.rows.items()
            if k.chain_id == cid
        ][:5]
        print(f"  Chain {cid}: {count} vaults (e.g. {', '.join(samples)})")

    total_stale = sum(chain_ids_in_db[cid] for cid in stale_chain_ids)
    print(f"\nTotal stale entries to remove: {total_stale}")
    print(f"Vault database size before: {len(vault_db.rows)}")
    print(f"Vault database size after:  {len(vault_db.rows) - total_stale}")

    # Check leads
    stale_leads = {spec for spec in vault_db.leads if spec.chain_id in stale_chain_ids}
    if stale_leads:
        print(f"Stale leads to remove: {len(stale_leads)}")

    # Check reader states
    reader_states_removed = 0
    if DEFAULT_READER_STATE_DATABASE.exists():
        reader_states: VaultReaderData = pickle.load(DEFAULT_READER_STATE_DATABASE.open("rb"))
        reader_states_removed = sum(1 for spec in reader_states if spec.chain_id in stale_chain_ids)
        if reader_states_removed:
            print(f"Stale reader states to remove: {reader_states_removed}")

    # Check price parquet files
    for label, parquet_path in [
        ("Uncleaned prices", DEFAULT_UNCLEANED_PRICE_DATABASE),
        ("Cleaned prices", DEFAULT_RAW_PRICE_DATABASE),
    ]:
        if parquet_path.exists():
            prices_df = pd.read_parquet(parquet_path)
            stale_rows = prices_df["chain"].isin(stale_chain_ids).sum()
            if stale_rows:
                print(f"{label} ({parquet_path.name}): {stale_rows:,} stale rows to remove")

    if dry_run:
        print("\nDry run — no changes made. Unset DRY_RUN to purge.")
        return

    confirmation = input("\nProceed with purge? [y/N]: ")
    if confirmation.lower() != "y":
        print("Aborted.")
        return

    # Purge vault database rows and leads
    vault_db.rows = {
        spec: row
        for spec, row in vault_db.rows.items()
        if spec.chain_id not in stale_chain_ids
    }
    vault_db.leads = {
        spec: lead
        for spec, lead in vault_db.leads.items()
        if spec.chain_id not in stale_chain_ids
    }
    for cid in stale_chain_ids:
        vault_db.last_scanned_block.pop(cid, None)

    vault_db.write()
    print(f"Wrote {DEFAULT_VAULT_DATABASE} ({len(vault_db.rows)} vaults)")

    # Purge reader states
    if DEFAULT_READER_STATE_DATABASE.exists() and reader_states_removed > 0:
        reader_states = pickle.load(DEFAULT_READER_STATE_DATABASE.open("rb"))
        new_reader_states = {
            spec: state
            for spec, state in reader_states.items()
            if spec.chain_id not in stale_chain_ids
        }
        pickle.dump(new_reader_states, DEFAULT_READER_STATE_DATABASE.open("wb"))
        print(f"Wrote {DEFAULT_READER_STATE_DATABASE} ({len(new_reader_states)} states)")

    # Purge price parquet files
    for label, parquet_path in [
        ("Uncleaned prices", DEFAULT_UNCLEANED_PRICE_DATABASE),
        ("Cleaned prices", DEFAULT_RAW_PRICE_DATABASE),
    ]:
        if parquet_path.exists():
            prices_df = pd.read_parquet(parquet_path)
            before = len(prices_df)
            prices_df = prices_df[~prices_df["chain"].isin(stale_chain_ids)]
            if len(prices_df) < before:
                prices_df.to_parquet(parquet_path)
                print(f"Wrote {parquet_path.name} ({before - len(prices_df):,} rows removed)")

    print("Done.")


if __name__ == "__main__":
    main()
