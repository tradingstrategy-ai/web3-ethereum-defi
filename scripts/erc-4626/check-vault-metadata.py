"""Check the written metadata of a vault.

- See our internal scanner database has correctly identifier a vault, its features and flags
"""

from pprint import pprint

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

# Foxify LP token on Sonic
vault_id = VaultSpec.parse_string("146-0x3ccff8c929b497c1ff96592b8ff592b45963e732")
vault_db = VaultDatabase.read()
vault_record = vault_db.get(vault_id)
pprint(vault_record)
