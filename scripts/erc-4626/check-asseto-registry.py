#!/usr/bin/env python3
"""Print Asseto's current EVM registry without modifying scanner state.

The command deliberately reads Asseto's public application endpoint directly:
it neither writes the persistent registry cache nor opens the vault database.
Use it before a production rollout to review supported chains, denominations
and public daily-NAV identities.
"""

import logging
import os

from tabulate import tabulate

from eth_defi.tokenised_fund.asseto.offchain_api import fetch_asseto_products
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Fetch and print the current parseable EVM Asseto registry."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    products = list(fetch_asseto_products())
    rows = [
        {
            "chain_id": product.chain_id,
            "address": product.contract_address,
            "symbol": product.symbol,
            "denomination": product.denomination_symbol,
            "product_id": product.product_id,
            "product_name": product.product_name,
        }
        for product in products
    ]
    logger.info("Asseto public EVM registry\n%s", tabulate(rows, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()
