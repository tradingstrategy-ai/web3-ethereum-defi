#!/usr/bin/env python3
# ruff: noqa: I001
"""Run tokenised-fund price backfills through the recurring scanner path.

The command defaults to a read-only plan. Set ``DRY_RUN=false`` only after
reviewing exact target addresses and their address-scoped start blocks.

Example::

    source .local-test.env
    DRY_RUN=false TOKENISED_FUND_PROTOCOLS=securitize \\
    TOKENISED_FUND_PRODUCTS=0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9 \\
    poetry run python scripts/erc-4626/backfill-tokenised-fund-prices.py
"""

from eth_defi.tokenised_fund.price_backfill import main


if __name__ == "__main__":
    main()
