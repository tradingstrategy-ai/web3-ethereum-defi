#!/usr/bin/env python3
# ruff: noqa: I001, N999
"""Run selected tokenised-fund protocol backfills.

Set ``PROTOCOLS`` to a comma-separated list of protocol slugs. It defaults to
all integrations. The aggregate command defaults to ``DRY_RUN=true``; set
``DRY_RUN=false`` explicitly to write metadata, reader state or Parquet data.
"""

from eth_defi.tokenised_fund.backfill import main


if __name__ == "__main__":
    main()
