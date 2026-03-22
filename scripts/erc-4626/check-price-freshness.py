"""Check freshness of cleaned vault price data.

Reads cleaned-vault-prices-1h.parquet and checks how recent the data is.
By default reads from the local file. Set ``PARQUET_URL`` to a URL to
load from a remote source (e.g. the production R2 bucket).

By default only shows high TVL (>= 20k) vaults. Set ``SHOW_LOW_TVL=true``
to also show low TVL vaults. Only high TVL freshness affects the exit code.

Computes the absolute last timestamp and the median last timestamp
(excluding outliers via IQR). Exits with code 1 if the high TVL median
age exceeds 36 hours (configurable via MAX_AGE_HOURS).

Usage:

.. code-block:: shell

    # Check local file (default)
    poetry run python scripts/erc-4626/check-price-freshness.py

    # Check production data
    PARQUET_URL=https://vault-protocol-metadata.tradingstrategy.ai/cleaned-vault-prices-1h.parquet poetry run python scripts/erc-4626/check-price-freshness.py

Environment variables:

- ``PARQUET_URL``: URL to load parquet from. Default: local file.
- ``MAX_AGE_HOURS``: Maximum allowed age in hours (default: 36)
- ``TVL_THRESHOLD``: TVL boundary between low and high in USD (default: 20000)
- ``SHOW_LOW_TVL``: Set to ``true`` to also show low TVL vault table (default: false)
- ``SHOW_UNCLEANED``: Set to ``true`` to also show uncleaned price data freshness (default: false)
"""

import io
import os
import sys

import pandas as pd
import requests
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE


def _ensure_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """Ensure a timestamp is tz-aware UTC."""
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts


def _format_age(td: pd.Timedelta) -> str:
    """Format a timedelta as a human-readable short string."""
    total_hours = td.total_seconds() / 3600
    if total_hours < 24:
        return f"{total_hours:.1f}h"
    days = int(total_hours // 24)
    hours = total_hours - days * 24
    return f"{days}d {hours:.0f}h"


def _compute_freshness(latest_per_vault: pd.Series, now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timedelta, pd.Timedelta]:
    """Compute absolute and median (IQR-filtered) freshness stats.

    :return: (abs_last, median_last, abs_age, median_age)
    """
    abs_last = _ensure_utc(latest_per_vault.max())

    # Remove outliers using IQR
    q1 = latest_per_vault.quantile(0.25)
    q3 = latest_per_vault.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    filtered = latest_per_vault[(latest_per_vault >= lower) & (latest_per_vault <= upper)]

    median_last = _ensure_utc(filtered.median())
    return abs_last, median_last, now - abs_last, now - median_last


def _build_chain_table(latest_per_vault: pd.Series, now: pd.Timestamp, written_at_per_vault: pd.Series | None = None) -> list[list]:
    """Build per-chain freshness rows for tabulate."""
    rows = []
    for chain_id, group in sorted(latest_per_vault.groupby(level="chain")):
        chain_name = get_chain_name(chain_id)
        vault_count = len(group)
        chain_abs_last = _ensure_utc(group.max())
        chain_median = _ensure_utc(group.median())

        # Latest written_at for this chain (when data was last fetched)
        if written_at_per_vault is not None:
            chain_written = written_at_per_vault.loc[written_at_per_vault.index.isin(group.index)]
            chain_written = chain_written.dropna()
            if len(chain_written) > 0:
                last_written = _ensure_utc(chain_written.max())
                written_str = f"{last_written} ({_format_age(now - last_written)})"
            else:
                written_str = "n/a"
        else:
            written_str = "n/a"

        rows.append(
            [
                chain_name,
                chain_id,
                vault_count,
                str(chain_abs_last),
                _format_age(now - chain_abs_last),
                written_str,
                str(chain_median),
                _format_age(now - chain_median),
            ]
        )
    return rows


def main():
    max_age_hours = int(os.environ.get("MAX_AGE_HOURS", "36"))
    tvl_threshold = float(os.environ.get("TVL_THRESHOLD", "20000"))
    show_low_tvl = os.environ.get("SHOW_LOW_TVL", "false").lower() == "true"
    show_uncleaned = os.environ.get("SHOW_UNCLEANED", "false").lower() == "true"
    parquet_url = os.environ.get("PARQUET_URL", "")

    if parquet_url:
        source = parquet_url
        print(f"Downloading {parquet_url}...")
        resp = requests.get(parquet_url, timeout=120)
        resp.raise_for_status()
        file_size = len(resp.content)
        df = pd.read_parquet(io.BytesIO(resp.content))
    else:
        # Default: use local file
        if not DEFAULT_RAW_PRICE_DATABASE.exists():
            print(f"Cleaned price file not found: {DEFAULT_RAW_PRICE_DATABASE}")
            sys.exit(1)
        source = str(DEFAULT_RAW_PRICE_DATABASE)
        file_size = DEFAULT_RAW_PRICE_DATABASE.stat().st_size
        df = pd.read_parquet(DEFAULT_RAW_PRICE_DATABASE)

    # Ensure timestamp is a column
    if "timestamp" not in df.columns and df.index.name == "timestamp":
        df = df.reset_index()

    now = pd.Timestamp.now("UTC")
    headers = ["Chain", "ID", "Vaults", "Last timestamp", "Age", "Written at", "Median timestamp", "Median age"]

    # Get latest TVL per vault (last row's total_assets)
    latest_idx = df.groupby(["chain", "address"])["timestamp"].idxmax()
    latest_tvl = df.loc[latest_idx].set_index(["chain", "address"])["total_assets"]

    # Get latest written_at per vault (when data was last fetched/written)
    if "written_at" in df.columns:
        written_at_per_vault = df.groupby(["chain", "address"])["written_at"].max()
    else:
        written_at_per_vault = None

    # Split vaults into high/low TVL
    high_tvl_vaults = latest_tvl[latest_tvl >= tvl_threshold].index
    low_tvl_vaults = latest_tvl[(latest_tvl < tvl_threshold) | latest_tvl.isna()].index

    # Latest timestamp per vault
    latest_per_vault = df.groupby(["chain", "address"])["timestamp"].max()

    high_latest = latest_per_vault[latest_per_vault.index.isin(high_tvl_vaults)]
    low_latest = latest_per_vault[latest_per_vault.index.isin(low_tvl_vaults)]

    file_size_mb = file_size / (1024 * 1024)
    print(f"Source: {source} ({file_size_mb:.1f} MB)")
    print(f"TVL threshold: ${tvl_threshold:,.0f}")

    # High TVL vaults
    high_abs, high_median, high_abs_age, high_median_age = _compute_freshness(high_latest, now)
    print(f"\nHigh TVL vaults (>= ${tvl_threshold:,.0f}): {len(high_latest)}")
    print(f"  Absolute last: {high_abs} (age: {_format_age(high_abs_age)})")
    print(f"  Median last:   {high_median} (age: {_format_age(high_median_age)})")
    print(tabulate(_build_chain_table(high_latest, now, written_at_per_vault), headers=headers, tablefmt="fancy_grid"))

    # Low TVL vaults (optional)
    if show_low_tvl:
        low_abs, low_median, low_abs_age, low_median_age = _compute_freshness(low_latest, now)
        print(f"\nLow TVL vaults (< ${tvl_threshold:,.0f}): {len(low_latest)}")
        print(f"  Absolute last: {low_abs} (age: {_format_age(low_abs_age)})")
        print(f"  Median last:   {low_median} (age: {_format_age(low_median_age)})")
        print(tabulate(_build_chain_table(low_latest, now, written_at_per_vault), headers=headers, tablefmt="fancy_grid"))
    else:
        print(f"\nLow TVL vaults (< ${tvl_threshold:,.0f}): {len(low_latest)} (set SHOW_LOW_TVL=true to display)")

    # Uncleaned price data (optional)
    if show_uncleaned:
        if DEFAULT_UNCLEANED_PRICE_DATABASE.exists():
            uncleaned_df = pd.read_parquet(DEFAULT_UNCLEANED_PRICE_DATABASE)
            if "timestamp" not in uncleaned_df.columns and uncleaned_df.index.name == "timestamp":
                uncleaned_df = uncleaned_df.reset_index()
            uncleaned_latest = uncleaned_df.groupby(["chain", "address"])["timestamp"].max()
            if "written_at" in uncleaned_df.columns:
                unc_written_at = uncleaned_df.groupby(["chain", "address"])["written_at"].max()
            else:
                unc_written_at = None
            unc_abs, unc_median, unc_abs_age, unc_median_age = _compute_freshness(uncleaned_latest, now)
            print(f"\nUncleaned price data ({DEFAULT_UNCLEANED_PRICE_DATABASE.name}): {len(uncleaned_latest)} vaults")
            print(f"  Absolute last: {unc_abs} (age: {_format_age(unc_abs_age)})")
            print(f"  Median last:   {unc_median} (age: {_format_age(unc_median_age)})")
            print(tabulate(_build_chain_table(uncleaned_latest, now, unc_written_at), headers=headers, tablefmt="fancy_grid"))
        else:
            print(f"\nUncleaned price file not found: {DEFAULT_UNCLEANED_PRICE_DATABASE}")

    # Exit code based on high TVL freshness only
    max_age = pd.Timedelta(hours=max_age_hours)
    if high_median_age > max_age:
        print(f"\nFAIL: High TVL median data age {_format_age(high_median_age)} exceeds {max_age_hours}h threshold")
        sys.exit(1)
    else:
        print(f"\nOK: High TVL data is fresh (threshold: {max_age_hours}h)")


if __name__ == "__main__":
    main()
