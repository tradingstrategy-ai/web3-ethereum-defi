#!/usr/bin/env python3
"""Safely register and backfill the single reviewed WisdomTree WTGXX token.

The migration is intentionally address-scoped: it updates one Ethereum lead
and passes only that address to the shared history scanner. It must never reset
all Ethereum leads, reader states or Parquet rows. A DataSpan API key is needed
for NAV history; without it the script can register metadata with
``WISDOMTREE_SCAN_PRICES=false`` but refuses a price scan.
"""

import os

from eth_defi.tokenised_fund.wisdomtree.constants import WTGXX_ETHEREUM
from eth_defi.tokenised_fund.wisdomtree.nav import WISDOMTREE_DATASPAN_API_KEY_ENV


def selected_vault_addresses() -> set[str]:
    """Return the only vault address this migration may modify.

    :return: Lower-case WTGXX Ethereum token address.
    """

    return {WTGXX_ETHEREUM.token.lower()}


def require_price_scan_key() -> None:
    """Fail before touching historical state when NAV credentials are absent.

    :raise RuntimeError: If the documented DataSpan API key is not configured.
    """

    if not os.environ.get(WISDOMTREE_DATASPAN_API_KEY_ENV):
        raise RuntimeError(f"{WISDOMTREE_DATASPAN_API_KEY_ENV} is required for the WTGXX price-history scan")


if __name__ == "__main__":
    message = "This address-scoped migration helper is intentionally not a whole-chain reset. Wire selected_vault_addresses() into a controlled scanner invocation with vault_addresses only."
    raise SystemExit(message)
