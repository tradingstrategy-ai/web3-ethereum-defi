"""Compatibility entrypoint for the scoped Antarctic metadata/history backfill.

See ``backfill-antarctic-vaults.py`` and README-Antarctic.md for the manual
operator command. Both entrypoints share the same tested implementation.
"""

from eth_defi.erc_4626.vault_protocol.antarctic.migration import main

if __name__ == "__main__":
    main()
