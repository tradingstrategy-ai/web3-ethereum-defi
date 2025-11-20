"""
GMX CCXT Market Data Examples

Examples of CCXT-compatible market data methods:
- fetch_ticker() - Single market ticker
- fetch_tickers() - Multiple market tickers
- fetch_currencies() - Token metadata
- fetch_trades() - Recent public trades
- fetch_time() - Server/blockchain time
- fetch_status() - API health status

Usage:
    python scripts/gmx/gmx_ccxt_market_data.py
"""

import os
from datetime import datetime, timedelta
from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX


def format_price(price):
    """Format price with appropriate precision."""
    if price is None:
        return "N/A"
    if price >= 1:
        return f"${price:,.2f}"
    elif price >= 0.01:
        return f"${price:,.4f}"
    elif price >= 0.0001:
        return f"${price:,.6f}"
    else:
        return f"${price:,.8f}"


def example_fetch_ticker(gmx: GMX):
    """Example: fetch_ticker() - Single market ticker"""
    print("\n" + "=" * 60)
    print("1. fetch_ticker() - Single Market Ticker")
    print("=" * 60)

    ticker = gmx.fetch_ticker("ETH/USD")

    print(f"Symbol: {ticker['symbol']}")
    print(f"Last Price: {format_price(ticker['last'])}")
    print(f"24h High: {format_price(ticker['high'])}")
    print(f"24h Low: {format_price(ticker['low'])}")
    print(f"24h Open: {format_price(ticker['open'])}")
    print(f"Time: {ticker['datetime']}")


def example_fetch_tickers(gmx: GMX):
    """Example: fetch_tickers() - Multiple market tickers"""
    print("\n" + "=" * 60)
    print("2. fetch_tickers() - Multiple Market Tickers")
    print("=" * 60)

    tickers = gmx.fetch_tickers()

    print(f"\nShowing first 10 of {len(tickers)} markets:\n")
    for symbol in sorted(tickers.keys())[:10]:
        ticker = tickers[symbol]
        print(f"{symbol:12} {format_price(ticker['last']):>15}  High: {format_price(ticker['high']):>15}  Low: {format_price(ticker['low']):>15}")

    # Example: filtering by symbols
    print("\nFiltering to specific symbols:")
    filtered = gmx.fetch_tickers(["ETH/USD", "BTC/USD"])
    for symbol, ticker in filtered.items():
        print(f"  {symbol}: {format_price(ticker['last'])}")


def example_fetch_currencies(gmx: GMX):
    """Example: fetch_currencies() - Token metadata"""
    print("\n" + "=" * 60)
    print("3. fetch_currencies() - Token Metadata")
    print("=" * 60)

    currencies = gmx.fetch_currencies()

    print(f"\nShowing first 10 of {len(currencies)} tokens:\n")
    for code in sorted(currencies.keys())[:10]:
        currency = currencies[code]
        address = currency["id"][:10] + "..." + currency["id"][-6:]
        print(f"{code:8} {currency['name'][:20]:20} Decimals: {currency['precision']:2}  Address: {address}")


def example_fetch_trades(gmx: GMX):
    """Example: fetch_trades() - Recent public trades"""
    print("\n" + "=" * 60)
    print("4. fetch_trades() - Recent Public Trades")
    print("=" * 60)

    since = int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)
    trades = gmx.fetch_trades("ETH/USD", since=since, limit=10)

    if trades:
        print(f"\nShowing {len(trades)} trades for ETH/USD:\n")
        for trade in trades[:10]:
            timestamp = datetime.fromisoformat(trade["datetime"].replace("Z", "+00:00"))
            time_str = timestamp.strftime("%m-%d %H:%M")
            side = trade["side"].upper()
            price = f"${trade['price']:,.2f}" if trade["price"] else "N/A"
            amount = f"{trade['amount']:.4f}" if trade["amount"] else "N/A"
            cost = f"${trade['cost']:,.2f}" if trade["cost"] else "N/A"
            print(f"{time_str}  {side:4}  Price: {price:>12}  Amount: {amount:>10}  Cost: {cost:>12}")
    else:
        print("No trades found in the last 24 hours")


def example_fetch_time(gmx: GMX):
    """Example: fetch_time() - Server/blockchain time"""
    print("\n" + "=" * 60)
    print("5. fetch_time() - Server/Blockchain Time")
    print("=" * 60)

    server_time = gmx.fetch_time()
    dt = datetime.fromtimestamp(server_time / 1000)
    local_time = datetime.now()
    diff_seconds = abs((dt - local_time).total_seconds())

    print(f"\nServer Time: {server_time:,} ms")
    print(f"Readable: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Local Time: {local_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Difference: {diff_seconds:.2f} seconds")


def example_fetch_status(gmx: GMX):
    """Example: fetch_status() - API health status"""
    print("\n" + "=" * 60)
    print("6. fetch_status() - API Health Status")
    print("=" * 60)

    status = gmx.fetch_status()

    print(f"\nOverall Status: {status['status'].upper()}")
    print(f"GMX API: {status['info'].get('gmx_api', 'N/A')}")
    print(f"Subsquid: {status['info'].get('subsquid', 'N/A')}")
    print(f"Web3: {status['info'].get('web3', 'N/A')}")

    if "web3_block_number" in status["info"]:
        print(f"Current Block: {status['info']['web3_block_number']:,}")

    print(f"Updated: {status['datetime']}")


def main():
    print("\n" + "=" * 60)
    print("GMX CCXT Market Data Examples")
    print("=" * 60)

    # Initialize GMX CCXT wrapper
    rpc = os.environ.get("JSON_RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc")

    web3 = Web3(Web3.HTTPProvider(rpc))
    config = GMXConfig(web3)
    gmx = GMX(config)

    print(f"\nChain ID: {web3.eth.chain_id}")
    print("Connected successfully")

    try:
        # Run all examples
        example_fetch_ticker(gmx)
        example_fetch_tickers(gmx)
        example_fetch_currencies(gmx)
        example_fetch_trades(gmx)
        example_fetch_time(gmx)
        example_fetch_status(gmx)

        print("\n" + "=" * 60)
        print("All market data methods executed successfully!")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
