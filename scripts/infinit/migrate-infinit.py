"""Migrate Infinit discovery, metadata and history; ``DRY_RUN=true`` by default.

Set ``DRY_RUN=false`` to apply the same validated operation. Existing cleaned
history is updated automatically. See ``scripts/infinit/README.md`` for usage.
"""

from eth_defi.erc_4626.vault_protocol.infinit.migration import main

if __name__ == "__main__":
    main()
