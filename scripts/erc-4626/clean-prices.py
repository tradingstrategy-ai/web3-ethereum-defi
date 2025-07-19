"""Clean raw scanned vault data.

- Reads ``vault-prices-1h.parquet`` and generates ``vault-prices-1h-cleaned.parquet``
- Calculate returns and various performance metrics to be included with prices data
- Clean returns from abnormalities

.. note::

    Drops non-stablecoin vaults. The cleaning is currently applicable
    for stable vaults only.
"""
from eth_defi.vault.wrangle import generate_cleaned_vault_datasets


def main():
    generate_cleaned_vault_datasets()


if __name__ == "__main__":
    main()