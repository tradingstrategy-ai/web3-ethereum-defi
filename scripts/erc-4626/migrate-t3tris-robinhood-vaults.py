#!/usr/bin/env python3
"""Perform the one-use T3tris Robinhood vault metadata migration.

This deliberately non-configurable migration repairs only the audited Morini
and Kingfisher T3tris vaults on Robinhood. It fixes the chain, addresses,
baked snapshot, metadata refresh and metadata-only mode in code. It cannot
reset discovery, select another chain, or rewrite price data.

Set DRY_RUN=true to inspect the migration before applying it.
"""

import os
import runpy
from pathlib import Path

#: Robinhood Chain.
ROBINHOOD_CHAIN_ID = "4663"

#: Audited T3tris Robinhood vaults, in deterministic order.
T3TRIS_ROBINHOOD_VAULT_ADDRESSES = (
    "0x5b93dd3eb7fd224565498045f5e1a2ebda49e672",
    "0xd4d607239dcbdb5cc3a301266433810bb63c63bf",
)


def main() -> None:
    """Run the fixed-scope T3tris Robinhood metadata repair.

    The shared repair owns the tested lead upsert and metadata classification
    flow. This one-use entrypoint fixes every operational scope setting before
    loading it, while preserving an operator-provided DRY_RUN value.
    """
    os.environ["T3TRIS_CHAIN_IDS"] = ROBINHOOD_CHAIN_ID
    os.environ["T3TRIS_VAULT_ADDRESSES"] = ",".join(T3TRIS_ROBINHOOD_VAULT_ADDRESSES)
    os.environ["T3TRIS_FETCH_API"] = "false"
    os.environ["T3TRIS_REFRESH_EXISTING_METADATA"] = "true"
    os.environ["T3TRIS_SCAN_PRICES"] = "false"
    runpy.run_path(Path(__file__).with_name("fix-t3tris-vaults.py"), run_name="__main__")


if __name__ == "__main__":
    main()
