#!/usr/bin/env python3
"""GMX CCXT Interface Example.

Example script using the CCXT-compatible interface for GMX protocol.
Fetches OHLCV (candlestick) data and analyzes it using pandas, numpy, and rich.

Usage::

    python scripts/gmx/gmx_ccxt_ohlcv.py

Requirements:
    - Web3 connection to Arbitrum network
    - No wallet/private key required (read-only operations)
"""

import sys
import time


from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import track
from rich import box

console = Console()


def ohlcv_to_dataframe(ohlcv, symbol):
    """Convert OHLCV list to pandas DataFrame.

    :param ohlcv: List of OHLCV candles
    :type ohlcv: list
    :param symbol: Trading symbol
    :type symbol: str
    :return: DataFrame with OHLCV data
    :rtype: pandas.DataFrame
    """
    df = pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["symbol"] = symbol
    return df


def calculate_technical_indicators(df):
    """Calculate basic technical indicators.

    :param df: DataFrame with OHLCV data
    :type df: pandas.DataFrame
    :return: DataFrame with added technical indicators
    :rtype: pandas.DataFrame
    """
    # Simple Moving Averages
    df["sma_10"] = df["close"].rolling(window=10).mean()
    df["sma_20"] = df["close"].rolling(window=20).mean()

    # Price changes
    df["change"] = df["close"].diff()
    df["change_pct"] = df["close"].pct_change() * 100

    # High-Low range
    df["hl_range"] = df["high"] - df["low"]
    df["hl_range_pct"] = (df["hl_range"] / df["close"]) * 100

    return df


def fetch_ohlcv_example():
    """Basic OHLCV fetching with DataFrame analysis.

    Demonstrates fetching OHLCV data for a single symbol and timeframe,
    converting to DataFrame, and displaying statistics.
    """
    console.print(
        Panel.fit("[bold cyan]Example 1: Fetch OHLCV Data[/bold cyan]", border_style="cyan"),
    )

    # Setup GMX connection (Arbitrum mainnet)
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)

    # Create CCXT-compatible wrapper
    gmx = GMX(config)

    # Load available markets
    console.print("\n[yellow]Loading markets...[/yellow]")
    markets = gmx.load_markets()
    console.print(f"[green]Loaded {len(markets)} markets[/green]")

    # Fetch OHLCV data for ETH/USDC:USDC
    console.print("\n" + "─" * 80)
    console.print("[bold]Fetching ETH/USDC:USDC hourly candles (last 50)...[/bold]")
    console.print("─" * 80 + "\n")

    symbol = "ETH/USDC:USDC"
    timeframe = "1h"
    limit = 50

    ohlcv = gmx.fetch_ohlcv(symbol, timeframe, limit=limit)

    # Convert to DataFrame
    df = ohlcv_to_dataframe(ohlcv, symbol)
    df = calculate_technical_indicators(df)

    console.print(
        f"[green]Received {len(ohlcv)} candles for {symbol} ({timeframe})[/green]\n",
    )

    # Display statistics table
    stats_table = Table(title=f"{symbol} Statistics", box=box.ROUNDED)
    stats_table.add_column("Metric", style="cyan", justify="left")
    stats_table.add_column("Value", style="green", justify="right")

    latest = df.iloc[-1]
    stats_table.add_row("Latest Price", f"${latest['close']:,.2f}")
    stats_table.add_row("24h High", f"${df['high'].tail(24).max():,.2f}")
    stats_table.add_row("24h Low", f"${df['low'].tail(24).min():,.2f}")
    stats_table.add_row("24h Change", f"{df['change_pct'].tail(24).sum():+.2f}%")
    stats_table.add_row("Avg HL Range", f"${df['hl_range'].mean():.2f}")
    stats_table.add_row("SMA 10", f"${latest['sma_10']:.2f}")
    stats_table.add_row("SMA 20", f"${latest['sma_20']:.2f}")

    console.print(stats_table)

    # Display recent candles
    console.print("\n")
    candles_table = Table(title="Recent Candles (Last 5)", box=box.SIMPLE)
    candles_table.add_column("Time", style="cyan")
    candles_table.add_column("Open", justify="right")
    candles_table.add_column("High", justify="right", style="green")
    candles_table.add_column("Low", justify="right", style="red")
    candles_table.add_column("Close", justify="right", style="bold")
    candles_table.add_column("Change %", justify="right")

    for _, row in df.tail(5).iterrows():
        change_style = "green" if row["change_pct"] >= 0 else "red"
        candles_table.add_row(
            row["timestamp"].strftime("%m-%d %H:%M"),
            f"${row['open']:,.2f}",
            f"${row['high']:,.2f}",
            f"${row['low']:,.2f}",
            f"${row['close']:,.2f}",
            f"[{change_style}]{row['change_pct']:+.2f}%[/{change_style}]",
        )

    console.print(candles_table)

    return df


def fetch_multiple_timeframes():
    """Fetch and compare multiple timeframes.

    Demonstrates fetching the same symbol across different timeframes
    and comparing the results in a single table.
    """
    console.print("\n\n")
    console.print(Panel.fit("[bold cyan]Example 2: Multiple Timeframes Analysis[/bold cyan]", border_style="cyan"))

    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMX(config)

    exchange.load_markets()

    symbol = "BTC/USDC:USDC"
    timeframes = ["1m", "15m", "1h", "1d"]

    console.print(
        f"\n[yellow]Fetching {symbol} data for multiple timeframes...[/yellow]\n",
    )

    # Create comparison table
    tf_table = Table(title=f"{symbol} Multi-Timeframe Analysis", box=box.ROUNDED)
    tf_table.add_column("Timeframe", style="cyan", justify="center")
    tf_table.add_column("Latest Price", justify="right")
    tf_table.add_column("Change %", justify="right")
    tf_table.add_column("High", justify="right", style="green")
    tf_table.add_column("Low", justify="right", style="red")
    tf_table.add_column("Volatility", justify="right")

    for tf in track(timeframes, description="Loading..."):
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=10)
        df = ohlcv_to_dataframe(ohlcv, symbol)
        df = calculate_technical_indicators(df)

        latest = df.iloc[-1]
        first = df.iloc[0]
        change_pct = ((latest["close"] - first["close"]) / first["close"]) * 100
        volatility = df["hl_range_pct"].mean()

        change_style = "green" if change_pct >= 0 else "red"
        tf_table.add_row(
            tf,
            f"${latest['close']:,.2f}",
            f"[{change_style}]{change_pct:+.2f}%[/{change_style}]",
            f"${df['high'].max():,.2f}",
            f"${df['low'].min():,.2f}",
            f"{volatility:.2f}%",
        )

    console.print(tf_table)


def fetch_with_since_parameter():
    """Fetch historical data and calculate returns.

    Demonstrates using the 'since' parameter to fetch data from a specific
    timestamp and calculating performance metrics over a time period.
    """
    console.print("\n\n")
    console.print(
        Panel.fit("[bold cyan]Example 3: Historical Analysis with 'since' Parameter[/bold cyan]", border_style="cyan"),
    )

    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMX(config)

    exchange.load_markets()

    symbol = "ETH/USDC:USDC"
    timeframe = "1h"

    # Calculate 'since' as 24 hours ago
    twenty_four_hours_ago = int((time.time() - 86400) * 1000)  # milliseconds

    console.print(
        f"\n[yellow]Fetching {symbol} {timeframe} candles from last 24 hours...[/yellow]\n",
    )

    ohlcv = exchange.fetch_ohlcv(
        symbol,
        timeframe,
        since=twenty_four_hours_ago,
        limit=24,
    )

    df = ohlcv_to_dataframe(ohlcv, symbol)
    df = calculate_technical_indicators(df)

    # Calculate performance metrics
    first_price = df.iloc[0]["close"]
    last_price = df.iloc[-1]["close"]
    price_change = last_price - first_price
    price_change_pct = (price_change / first_price) * 100

    max_price = df["high"].max()
    min_price = df["low"].min()
    avg_price = df["close"].mean()
    std_dev = df["close"].std()

    # Display performance panel
    perf_table = Table(title=f"24-Hour Performance: {symbol}", box=box.ROUNDED)
    perf_table.add_column("Metric", style="cyan", justify="left")
    perf_table.add_column("Value", style="bold", justify="right")

    perf_table.add_row("Start Price", f"${first_price:,.2f}")
    perf_table.add_row("End Price", f"${last_price:,.2f}")

    change_style = "green" if price_change >= 0 else "red"
    perf_table.add_row("24h Change", f"[{change_style}]{price_change:+,.2f} ({price_change_pct:+.2f}%)[/{change_style}]")

    perf_table.add_row("─" * 20, "─" * 15)
    perf_table.add_row("24h High", f"${max_price:,.2f}")
    perf_table.add_row("24h Low", f"${min_price:,.2f}")
    perf_table.add_row("Average", f"${avg_price:,.2f}")
    perf_table.add_row("Std Dev", f"${std_dev:.2f}")
    perf_table.add_row("Volatility", f"{(std_dev / avg_price * 100):.2f}%")

    console.print(perf_table)


def compare_multiple_tokens():
    """Compare multiple tokens with correlation analysis.

    Demonstrates fetching data for multiple tokens, comparing their performance,
    and calculating correlation between their price movements.
    """
    console.print("\n\n")
    console.print(
        Panel.fit("[bold cyan]Example 4: Multi-Token Comparison & Correlation[/bold cyan]", border_style="cyan"),
    )

    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMX(config)

    exchange.load_markets()

    symbols = ["ETH/USDC:USDC", "BTC/USDC:USDC", "ARB/USDC:USDC"]
    timeframe = "1h"
    limit = 24

    console.print(f"\n[yellow]Fetching hourly data for {len(symbols)} tokens...[/yellow]\n")

    # Collect data for all symbols
    dfs = {}
    for symbol in track(symbols, description="Loading..."):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = ohlcv_to_dataframe(ohlcv, symbol)
            df = calculate_technical_indicators(df)
            dfs[symbol] = df
        except Exception as e:
            console.print(f"[red]Error fetching {symbol}: {e}[/red]")

    # Create comparison table
    comp_table = Table(title="Token Comparison (24h)", box=box.ROUNDED)
    comp_table.add_column("Token", style="cyan", justify="left")
    comp_table.add_column("Current Price", justify="right")
    comp_table.add_column("24h Change", justify="right")
    comp_table.add_column("24h Return %", justify="right")
    comp_table.add_column("Volatility", justify="right")
    comp_table.add_column("Trend", justify="center")

    for symbol, df in dfs.items():
        if len(df) >= 2:
            first_price = df.iloc[0]["close"]
            latest_price = df.iloc[-1]["close"]
            change = latest_price - first_price
            change_pct = (change / first_price) * 100
            volatility = df["hl_range_pct"].mean()

            # Determine trend based on SMA
            sma_10 = df["sma_10"].iloc[-1]
            sma_20 = df["sma_20"].iloc[-1]
            if pd.notna(sma_10) and pd.notna(sma_20):
                if sma_10 > sma_20:
                    trend = "Bullish"
                    trend_style = "green"
                else:
                    trend = "Bearish"
                    trend_style = "red"
            else:
                trend = "Neutral"
                trend_style = "yellow"

            change_style = "green" if change >= 0 else "red"
            comp_table.add_row(
                symbol.replace("/USD", ""),
                f"${latest_price:,.2f}",
                f"[{change_style}]${change:+,.2f}[/{change_style}]",
                f"[{change_style}]{change_pct:+.2f}%[/{change_style}]",
                f"{volatility:.2f}%",
                f"[{trend_style}]{trend}[/{trend_style}]",
            )

    console.print(comp_table)

    # Calculate correlation matrix if we have enough data
    if len(dfs) >= 2:
        console.print("\n")
        corr_table = Table(title="Price Correlation Matrix", box=box.ROUNDED)
        corr_table.add_column("", style="cyan")

        # Prepare data for correlation
        price_data = {}
        for symbol, df in dfs.items():
            token = symbol.replace("/USD", "")
            price_data[token] = df["close"].values
            corr_table.add_column(token, justify="center")

        # Calculate correlations
        tokens = list(price_data.keys())
        for token1 in tokens:
            row = [token1]
            for token2 in tokens:
                if token1 == token2:
                    row.append("[bold]1.00[/bold]")
                else:
                    # Calculate correlation
                    corr = np.corrcoef(price_data[token1], price_data[token2])[0, 1]
                    if corr > 0.7:
                        style = "green"
                    elif corr < 0.3:
                        style = "red"
                    else:
                        style = "yellow"
                    row.append(f"[{style}]{corr:.2f}[/{style}]")
            corr_table.add_row(*row)

        console.print(corr_table)


def main():
    """Run all examples.

    Executes all OHLCV examples demonstrating different features
    of the GMX CCXT wrapper interface.
    """
    console.print("\n")
    console.print(
        Panel.fit(
            "[bold magenta]GMX CCXT-Compatible Interface Examples[/bold magenta]\n[dim]CCXT-style methods with pandas, numpy[/dim]",
            border_style="magenta",
            padding=(1, 2),
        )
    )

    try:
        # Run examples
        fetch_ohlcv_example()
        fetch_multiple_timeframes()
        fetch_with_since_parameter()
        compare_multiple_tokens()

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        import traceback

        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
