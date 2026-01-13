"""Check the written metadata of a vault.

- See our internal scanner database has correctly identifier a vault, its features and flags
"""

from pprint import pprint

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

# Foxify LP token on Sonic
vault_id = VaultSpec.parse_string("143-0x0a4AfB907672279926c73Dc1F77151931c2A55cC")
vault_db = VaultDatabase.read()
vault_record = vault_db.get(vault_id)
pprint(vault_record)
