"""Check freshness of cleaned vault price data.

Reads cleaned-vault-prices-1h.parquet and checks how recent the data is.
By default reads from the local file. Set ``PARQUET_URL`` to a URL to
load from a remote source (e.g. the production R2 bucket).

Computes the absolute last timestamp and the median last timestamp
(excluding outliers via IQR). Exits with code 1 if the median age
exceeds 24 hours (configurable via MAX_AGE_HOURS).

Usage:

.. code-block:: shell

    # Check local file (default)
    poetry run python scripts/erc-4626/check-price-freshness.py

    # Check production data
    PARQUET_URL=https://vault-protocol-metadata.tradingstrategy.ai/cleaned-vault-prices-1h.parquet \
      poetry run python scripts/erc-4626/check-price-freshness.py

Environment variables:

- ``PARQUET_URL``: URL to load parquet from. Default: local file.
- ``MAX_AGE_HOURS``: Maximum allowed age in hours (default: 24)
"""

import io
import os
import sys

import pandas as pd
import requests

from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE


def main():
    max_age_hours = int(os.environ.get("MAX_AGE_HOURS", "24"))
    parquet_url = os.environ.get("PARQUET_URL", "")

    if parquet_url:
        source = parquet_url
        print(f"Downloading {parquet_url}...")
        resp = requests.get(parquet_url, timeout=120)
        resp.raise_for_status()
        df = pd.read_parquet(io.BytesIO(resp.content))
    else:
        # Default: use local file
        if not DEFAULT_RAW_PRICE_DATABASE.exists():
            print(f"Cleaned price file not found: {DEFAULT_RAW_PRICE_DATABASE}")
            sys.exit(1)
        source = str(DEFAULT_RAW_PRICE_DATABASE)
        df = pd.read_parquet(DEFAULT_RAW_PRICE_DATABASE)

    # Ensure timestamp is a column
    if "timestamp" not in df.columns and df.index.name == "timestamp":
        df = df.reset_index()

    # Latest timestamp per vault
    latest_per_vault = df.groupby(["chain", "address"])["timestamp"].max()

    # Absolute last timestamp (before outlier removal)
    abs_last = latest_per_vault.max()

    # Remove outliers using IQR
    q1 = latest_per_vault.quantile(0.25)
    q3 = latest_per_vault.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    filtered = latest_per_vault[(latest_per_vault >= lower) & (latest_per_vault <= upper)]

    median_last = filtered.median()
    now = pd.Timestamp.now("UTC")

    # Ensure timestamps are tz-aware for subtraction
    if abs_last.tzinfo is None:
        abs_last = abs_last.tz_localize("UTC")
    if median_last.tzinfo is None:
        median_last = median_last.tz_localize("UTC")

    abs_age = now - abs_last
    median_age = now - median_last

    print(f"Source: {source}")
    print(f"Vaults total: {len(latest_per_vault)}, after outlier removal: {len(filtered)}")
    print(f"Absolute last timestamp: {abs_last} (age: {abs_age})")
    print(f"Median last timestamp:   {median_last} (age: {median_age})")

    max_age = pd.Timedelta(hours=max_age_hours)
    if median_age > max_age:
        print(f"\nFAIL: Median data age {median_age} exceeds {max_age_hours}h threshold")
        sys.exit(1)
    else:
        print(f"\nOK: Data is fresh (threshold: {max_age_hours}h)")


if __name__ == "__main__":
    main()
