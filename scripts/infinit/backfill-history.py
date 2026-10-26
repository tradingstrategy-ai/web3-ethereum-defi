"""Backfill only reviewed Infinit history; DRY_RUN=true by default.

Set CLEAN_PRICES=true to regenerate only its cleaned history too. See
docs/source/vaults/infinit/index.rst for isolated rehearsal and mounted deployment.
"""

from eth_defi.erc_4626.vault_protocol.infinit.migration import main

if __name__ == "__main__":
    main("history")
