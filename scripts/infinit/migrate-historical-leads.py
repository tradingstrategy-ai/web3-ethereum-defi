"""Recover reviewed Infinit deployment and manager activity; DRY_RUN=true by default."""

from eth_defi.erc_4626.vault_protocol.infinit.migration import main

if __name__ == "__main__":
    main("leads")
