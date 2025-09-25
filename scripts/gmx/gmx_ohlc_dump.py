#!/usr/bin/env python3
"""
GMX Historical OHLC Data Dump

Simple script to collect real GMX OHLC data using eth_defi's GMX integration.
Based on: https://web3-ethereum-defi.readthedocs.io/tutorials/gmx-v2-price-analysis.html

Usage:
    python scripts/gmx_ohlc_dump.py

Output:
    - Creates ~/Downloads directory
    - Saves {chain}_{symbol}_{timeframe}.parquet files with real GMX data
"""

import os
import pandas as pd
from pathlib import Path
import time
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.data import GMXMarketData
̦
console = Console()

# Available GMX timeframes: 1m, 5m, 15m, 1h, 4h, 1d
TIMEFRAMES = ["1h", "4h", "1d"]
CHAINS = {
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
}


def get_gmx_ohlc_data(config: GMXConfig, token_symbol: str = "ETH", period: str = "1h") -> pd.DataFrame:
    """Fetch OHLC (Open, High, Low, Close) price data from GMX API.

    This function is from the eth_defi tutorial and gets real historical data.
    """
    gmx_api = GMXAPI(config)

    # Request candlestick data from GMX API
    raw_data = gmx_api.get_candlesticks(token_symbol, period)

    if not raw_data or "candles" not in raw_data:
        console.print(f"No candlestick data received for {token_symbol}")
        return pd.DataFrame()

    candles = raw_data["candles"]
    if not candles:
        console.print(f"Empty candles array for {token_symbol}")
        return pd.DataFrame()

    # Validate data structure - ensure we have at least OHLC data
    num_fields = len(candles[0]) if candles else 0

    if num_fields >= 5:
        # Standard OHLC format: timestamp, open, high, low, close
        columns = ["timestamp", "open", "high", "low", "close"]

        df = pd.DataFrame(candles, columns=columns)
        # Convert Unix timestamps to Python datetime objects
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        console.print(f"Successfully retrieved {len(df)} {period} candles for {token_symbol}")
        return df

    console.print(f"Insufficient data fields ({num_fields}) for {token_symbol}")
    return pd.DataFrame()


def get_available_symbols(config: GMXConfig) -> list:
    """Get available trading symbols from GMX."""
    try:
        gmx_data = GMXMarketData(config)
        markets = gmx_data.get_available_markets()

        # Extract symbols, filter out swap markets
        symbols = []
        for market_address, market_info in markets.items():
            symbol = market_info.get("market_symbol", "")
            if not symbol.startswith("SWAP") and symbol not in ["", "UNKNOWN"]:
                symbols.append(symbol)

        console.print(f"Found {len(symbols)} available symbols: {symbols}")
        return symbols

    except Exception as e:
        console.print(f"Error getting symbols: {e}")
        # Fallback to known major symbols
        return ["ETH", "BTC", "LINK", "ARB", "AVAX", "SOL", "UNI"]


def combine_and_save_all_data(all_data: list, timeframe: str):
    """Combine all collected data and save as a single Parquet file with maximum compression."""

    if not all_data:
        console.print(f"No data to save for {timeframe}")
        return None

    # Combine all DataFrames
    console.print(f"\nCombining {len(all_data)} datasets for {timeframe}...")
    combined_df = pd.concat(all_data, ignore_index=True)

    # Sort by symbol and timestamp for better compression
    combined_df = combined_df.sort_values(by=["symbol", "timestamp"])

    # Save to Downloads
    filename = f"gmx-ohlc-{timeframe}.parquet"
    filepath = Path.home() / "Downloads" / filename

    console.print(f"Saving {len(combined_df)} records to {filepath}...")
    combined_df.to_parquet(
        filepath,
        compression="zstd",
        compression_level=22,
        index=False,
    )

    file_size = filepath.stat().st_size / 1024 / 1024
    console.print(f"Saved {filename}: {len(combined_df):,} records, {file_size:.2f}MB")
    return filepath


def collect_chain_data(chain: str, rpc_url: str, timeframe: str) -> list:
    """Collect OHLC data for one chain and timeframe."""
    console.print(f"\nProcessing {chain.upper()} {timeframe}")

    # Setup connection
    web3 = create_multi_provider_web3(rpc_url)
    config = GMXConfig(web3)

    console.print(f"Connected to {chain} (Chain ID: {web3.eth.chain_id})")

    # Get available symbols
    symbols = get_available_symbols(config)

    if not symbols:
        print(f"No symbols found for {chain}")
        return []

    chain_data = []

    # Collect data for each symbol with a fancy progress bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"[cyan]Collecting {chain} {timeframe} data...", total=len(symbols))

        for symbol in symbols:
            # Skip deprecated APE token
            if symbol == "APE_DEPRECATED":
                progress.advance(task)
                continue

            try:
                progress.update(task, description=f"[cyan]Fetching {chain} {symbol} {timeframe}")

                # Get real OHLC data from GMX API
                df = get_gmx_ohlc_data(config, symbol, timeframe)

                if not df.empty:
                    # Add metadata columns
                    df = df.copy()
                    df["chain"] = chain
                    df["symbol"] = symbol
                    df["timeframe"] = timeframe
                    df["collected_at"] = datetime.now()

                    chain_data.append(df)
                    # console.print(f"{symbol}: {len(df)} candles", style="green")
                else:
                    console.print(f"{symbol}: No data returned", style="yellow")

                # Rate limiting to be nice to the API
                time.sleep(0.5)
                progress.advance(task)

            except Exception as e:
                console.print(f"{symbol}: Error - {e}", style="red")
                progress.advance(task)
                continue

    console.print(f"Completed {chain} {timeframe}: {len(chain_data)} symbols collected")
    return chain_data


def main():
    console.print("Starting GMX OHLC data collection...")
    console.print(f"Timeframes: {TIMEFRAMES}")

    # Check for RPC URL environment variables
    arbitrum_rpc = os.environ.get("JSON_RPC_ARBITRUM")
    if not arbitrum_rpc:
        console.print("JSON_RPC_ARBITRUM not found in environment")
        arbitrum_rpc = CHAINS["arbitrum"]
        console.print(f"Using default RPC: {arbitrum_rpc}")
    else:
        console.print("Using RPC from environment variable")

    avalanche_rpc = os.environ.get("JSON_RPC_AVALANCHE", CHAINS["avalanche"])

    created_files = []

    # Process each timeframe separately to create one file per timeframe
    for timeframe in TIMEFRAMES:
        print(f"\n{'=' * 60}")
        print(f"COLLECTING {timeframe.upper()} DATA")
        print(f"{'=' * 60}")

        all_timeframe_data = []

        # Collect Arbitrum data
        try:
            arbitrum_data = collect_chain_data("arbitrum", arbitrum_rpc, timeframe)
            all_timeframe_data.extend(arbitrum_data)
        except Exception as e:
            console.print(f"Failed to process Arbitrum {timeframe}: {e}")

        # Collect Avalanche data
        try:
            avalanche_data = collect_chain_data("avalanche", avalanche_rpc, timeframe)
            all_timeframe_data.extend(avalanche_data)
        except Exception as e:
            console.print(f"Failed to process Avalanche {timeframe}: {e}")

        # Combine and save all data for this timeframe
        if all_timeframe_data:
            filepath = combine_and_save_all_data(all_timeframe_data, timeframe)
            if filepath:
                created_files.append(filepath)
        else:
            console.print(f"No data collected for {timeframe}")

    console.print(f"Created {len(created_files)} files in ~/Downloads/:")

    total_size = 0
    for filepath in created_files:
        if filepath.exists():
            size_mb = filepath.stat().st_size / 1024 / 1024
            total_size += size_mb

            # Quick data preview
            try:
                df = pd.read_parquet(filepath)
                symbols = df["symbol"].nunique()
                chains = df["chain"].nunique()
                records = len(df)
                date_range = f"{df['timestamp'].min().date()} to {df['timestamp'].max().date()}"

                console.print(f"   {filepath.name}")
                console.print(f"     {records:,} records, {symbols} symbols, {chains} chains")
                console.print(f"     {date_range}, {size_mb:.1f}MB")
            except:
                console.print(f"   {filepath.name} ({size_mb:.1f}MB)")

    console.print(f"\nTotal size: {total_size:.1f}MB")
    console.print(f"\nGMX OHLC data collection completed!")
    console.print(f"Files ready to copy: ~/Downloads/gmx-ohlc-*.parquet")


if __name__ == "__main__":
    main()
