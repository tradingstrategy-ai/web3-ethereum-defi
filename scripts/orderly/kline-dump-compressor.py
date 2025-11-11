"""Orderly kline dump parser.

- Orderly kline is the historical data of their trades
- Available as CSV file as a request
- This script reads and compresses it to a more efficient format
- We take only one of the timeframes
- Kline data doesn't contain open interest

To run:

.. code-block:: shell

    python scripts/orderly/kline-dump-compressor.py

Example data:

.. code-block:: csv

    0.00000000,34500.00000000,0.00000000,0.00000000,"PERP_BTC_USDC","1m",1698314040000,1698314100000
    34500.00000000,34500.00000000,34500.00000000,34500.00000000,0.00000000,0.00000000,"PERP_BTC_USDC","1m",1698315360000,1698315420000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698315540000,1698315600000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698313680000,1698313740000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698313860000,1698313920000

"""

import json
from pathlib import Path

import pandas as pd


# Define column names
columns = ["open", "high", "low", "close", "volume_usd", "volume_unit", "symbol", "interval", "start", "end"]

fname = Path.home() / "Downloads" / "kline_his0527.csv"

new_freq = "5min"

# Read CSV
df = pd.read_csv(fname, names=columns)

# Convert 'start' to datetime (assuming ms)
df["timestamp"] = pd.to_datetime(df["start"], unit="ms")
df = df.set_index("timestamp")

# Resample to 15-minute bars (example: OHLCV for each symbol)
resampled = df.groupby("symbol").resample(new_freq).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume_usd": "sum", "volume_unit": "sum"}).dropna().reset_index()

print(f"First entry at {resampled['timestamp'].min()}")
print(f"Last entry at {resampled['timestamp'].max()}")

print("Pairs:")
pairs = list(resampled["symbol"].unique())
print(json.dumps(pairs, indent=2))

print("Resampled data:")
print(resampled.loc[resampled.symbol == "PERP_BITCOIN_USDC"].head(3))

print(f"Total {resampled['symbol'].nunique()} trading pairs")


# Have data in logical order for better compression
resampled = resampled.sort_values(by=["symbol", "timestamp"])

# Parquest does not support MultiIndex
resampled = resampled.set_index("timestamp")

# Write to Parquet
target_file = Path.home() / "Downloads" / f"orderly-ohlcv-{new_freq}.parquet"
resampled.to_parquet(
    target_file,
    compression="zstd",
    compression_level=22,
)

print(f"Wrote {target_file}, size {target_file.stat().st_size / 1024**2:.2f} MiB")
