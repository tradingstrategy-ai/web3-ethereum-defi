"""Orderly kline dump parser.

- Orderly kline is the historical data of their trades
- Available as CSV file as a request
- This script reads and compresses it to a more efficient format
- We take only one of the timeframes
- Kline data doesn't contain open interest

Example data:

.. code-block:: csv

    0.00000000,34500.00000000,0.00000000,0.00000000,"PERP_BTC_USDC","1m",1698314040000,1698314100000
    34500.00000000,34500.00000000,34500.00000000,34500.00000000,0.00000000,0.00000000,"PERP_BTC_USDC","1m",1698315360000,1698315420000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698315540000,1698315600000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698313680000,1698313740000
    1842.95000000,1842.95000000,1842.95000000,1842.95000000,0.00000000,0.00000000,"PERP_ETH_USDC","1m",1698313860000,1698313920000

"""

from pathlib import Path

import pandas as pd


# Define column names
columns = ["open", "high", "low", "close", "volume", "volume_2", "symbol", "interval", "start", "end"]

fname = Path.home() / "Downloads" / "kline_his0527.csv"

new_freq = "15min"  # 15 minutes

# Read CSV
df = pd.read_csv(fname, names=columns)

# Convert 'start' to datetime (assuming ms)
df["timestamp"] = pd.to_datetime(df["start"], unit="ms")
df = df.set_index("timestamp")

# Resample to 15-minute bars (example: OHLCV for each symbol)
resampled = df.groupby("symbol").resample(new_freq).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "volume_2": "sum"}).dropna().reset_index()


print("Resampled data:")
print(resampled.head(3))

print(f"Total {resampled['symbol'].nunique()} trading pairs")

# Have data in logical order for better compression
resampled = resampled.sort_values(by=["symbol", "timestamp"])

# Parquest does not support MultiIndex
resampled = resampled.set_index("timestamp")

# Write to Parquet
resampled.to_parquet(
    Path.home() / "Downloads" / "orderly-ohlcv.parquet",
    compression="zstd",
    compression_level=22,
)
