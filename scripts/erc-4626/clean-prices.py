"""Clean raw scanned vault data.

- Reads ``vault-prices-1h.parquet`` and generates ``vault-prices-1h-cleaned.parquet``
- Calculate returns and various performance metrics to be included with prices data
- Clean returns from abnormalities

.. note::

    Drops non-stablecoin vaults. The cleaning is currently applicable
    for stable vaults only.

To test:

.. code-block:: shell

    python scripts/erc-4626/clean-prices.py

Debug run:

.. code-block:: shell

    VAULT_ID=1-"0x00c8a649c9837523ebb406ceb17a6378ab5c74cf" python scripts/erc-4626/clean-prices.py

"""

import os
from pathlib import Path

from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging


def main():
    print("Starting to clean vault prices data")

    # Print more information about which vault is being debugged
    diagnose_vault_id = os.environ.get("VAULT_ID")

    if diagnose_vault_id:
        # Enable debug prints for a particular vault
        logger = setup_console_logging(
            default_log_level="info",
        )
        logger.info("Diagnosing vault ID: %s", diagnose_vault_id)
    else:
        # Normal logging
        default_log_level = os.environ.get("LOG_LEVEL", "warning")
        logger = setup_console_logging(
            log_file=Path("logs") / "clean-prices.log",
            clear_log_file=True,
            default_log_level=default_log_level,
        )
        logger.info(
            "Using console log level: %s",
            default_log_level,
        )

    generate_cleaned_vault_datasets(diagnose_vault_id=diagnose_vault_id)


if __name__ == "__main__":
    main()
