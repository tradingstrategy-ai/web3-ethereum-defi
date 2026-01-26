"""Check the written metadata of a vault.

- See our internal scanner database has correctly identifier a vault, its features and flags
"""

import pickle
import sys
from pathlib import Path
from pprint import pprint

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, VaultDatabase

# Harvest
# https://tradingstrategy.ai/trading-view/vaults/harvest-usdc-vault-0x0f6d
vault_id = VaultSpec.parse_string("8453-0x0f6d1d626fd6284c6c1c1345f30996b89b879689")
vault_db = VaultDatabase.read()
vault_record = vault_db.get(vault_id)
print("Stored metadata:")
pprint(vault_record)

# Check reader status
reader_state_db = Path(DEFAULT_READER_STATE_DATABASE)
if not reader_state_db.exists():
    print("No reader state database found, skipping reader state check")
    sys.exit(1)

reader_states = pickle.load(reader_state_db.open("rb"))
unique_chains = set(spec.chain_id for spec in reader_states.keys())
print(f"Loaded {len(reader_states)} reader states from {reader_state_db}, contains {len(unique_chains)} chains")

# Display reader state for the selected vault
if vault_id in reader_states:
    print(f"\nReader state for {vault_id}:")
    pprint(reader_states[vault_id])
else:
    print(f"\nNo reader state found for {vault_id}")
