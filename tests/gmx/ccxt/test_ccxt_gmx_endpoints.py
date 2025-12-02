"""Test GMX CCXT endpoint functionality with real API calls.

These tests verify that the GMX class properly implements
all CCXT methods.
"""


def test_fetch_markets(gmx_arbitrum):
    """Test fetch_markets returns list of markets in CCXT format."""
    markets = gmx_arbitrum.fetch_markets()

    assert isinstance(markets, list)
    assert len(markets) > 0

    # Check first market has CCXT structure
    market = markets[0]
    assert "id" in market
    assert "symbol" in market
    assert "base" in market
    assert "quote" in market
    assert "active" in market
    assert "type" in market
    assert "precision" in market
    assert "limits" in market
    assert "info" in market

    # Verify it's a swap market
    assert market["type"] == "swap"
    assert market["swap"] is True
    assert market["spot"] is False


def test_load_markets(gmx_arbitrum):
    """Test load_markets caches and returns dict of markets."""
    markets = gmx_arbitrum.load_markets()

    assert isinstance(markets, dict)
    assert len(markets) > 0

    # Check markets are keyed by symbol (GMX uses USDC as quote currency)
    assert "ETH/USDC" in markets or "BTC/USDC" in markets

    # Verify caching
    assert gmx_arbitrum.markets_loaded is True
    assert len(gmx_arbitrum.markets) == len(markets)


def test_fetch_ticker(gmx_arbitrum):
    """Test fetch_ticker returns ticker data for a symbol."""
    gmx_arbitrum.load_markets()

    ticker = gmx_arbitrum.fetch_ticker("ETH/USDC")

    assert isinstance(ticker, dict)
    assert ticker["symbol"] == "ETH/USDC"
    assert "last" in ticker
    assert "bid" in ticker
    assert "ask" in ticker
    assert "high" in ticker
    assert "low" in ticker
    assert "timestamp" in ticker
    assert "datetime" in ticker

    # Verify price data is numeric
    assert isinstance(ticker["last"], (int, float))
    assert ticker["last"] > 0


def test_fetch_tickers(gmx_arbitrum):
    """Test fetch_tickers returns multiple ticker data."""
    gmx_arbitrum.load_markets()

    symbols = ["ETH/USDC", "BTC/USDC"]
    tickers = gmx_arbitrum.fetch_tickers(symbols)

    assert isinstance(tickers, dict)
    assert len(tickers) >= 2

    # Check both symbols are present
    assert "ETH/USDC" in tickers
    assert "BTC/USDC" in tickers

    # Verify ticker structure
    eth_ticker = tickers["ETH/USDC"]
    assert eth_ticker["symbol"] == "ETH/USDC"
    assert isinstance(eth_ticker["last"], (int, float))


def test_fetch_ohlcv(gmx_arbitrum):
    """Test fetch_ohlcv returns candlestick data."""
    gmx_arbitrum.load_markets()

    ohlcv = gmx_arbitrum.fetch_ohlcv("ETH/USDC", timeframe="1h", limit=10)

    assert isinstance(ohlcv, list)
    assert len(ohlcv) > 0
    assert len(ohlcv) <= 10

    # Check OHLCV structure: [timestamp, open, high, low, close, volume]
    candle = ohlcv[0]
    assert isinstance(candle, list)
    assert len(candle) == 6

    timestamp, open_price, high, low, close, volume = candle

    # Verify data types and values
    assert isinstance(timestamp, (int, float))
    assert isinstance(open_price, (int, float))
    assert isinstance(high, (int, float))
    assert isinstance(low, (int, float))
    assert isinstance(close, (int, float))

    # Verify price relationships
    assert high >= open_price
    assert high >= close
    assert high >= low
    assert low <= open_price
    assert low <= close


def test_fetch_funding_rate(gmx_arbitrum):
    """Test fetch_funding_rate returns current funding rate."""
    gmx_arbitrum.load_markets()

    funding = gmx_arbitrum.fetch_funding_rate("ETH/USDC")

    assert isinstance(funding, dict)
    assert funding["symbol"] == "ETH/USDC"
    assert "fundingRate" in funding
    assert "longFundingRate" in funding
    assert "shortFundingRate" in funding
    assert "fundingTimestamp" in funding
    assert "timestamp" in funding

    # Verify rates are numeric
    assert isinstance(funding["fundingRate"], (int, float))
    assert isinstance(funding["longFundingRate"], (int, float))
    assert isinstance(funding["shortFundingRate"], (int, float))


def test_fetch_funding_rate_history(gmx_arbitrum):
    """Test fetch_funding_rate_history returns historical funding rates."""
    gmx_arbitrum.load_markets()

    history = gmx_arbitrum.fetch_funding_rate_history("ETH/USDC", limit=5)

    assert isinstance(history, list)
    assert len(history) > 0
    assert len(history) <= 5

    # Check structure of each snapshot
    snapshot = history[0]
    assert snapshot["symbol"] == "ETH/USD"
    assert "fundingRate" in snapshot
    assert "longFundingRate" in snapshot
    assert "shortFundingRate" in snapshot
    assert "timestamp" in snapshot


def test_fetch_open_interest(gmx_arbitrum):
    """Test fetch_open_interest returns current OI data."""
    gmx_arbitrum.load_markets()

    oi = gmx_arbitrum.fetch_open_interest("ETH/USD")

    assert isinstance(oi, dict)
    assert oi["symbol"] == "ETH/USD"
    assert "openInterestAmount" in oi
    assert "openInterestValue" in oi
    assert "timestamp" in oi
    assert "info" in oi

    # Verify OI values are numeric
    assert isinstance(oi["openInterestAmount"], (int, float))
    assert isinstance(oi["openInterestValue"], (int, float))


def test_fetch_open_interest_history(gmx_arbitrum):
    """Test fetch_open_interest_history returns historical OI."""
    gmx_arbitrum.load_markets()

    history = gmx_arbitrum.fetch_open_interest_history("BTC/USD", limit=5)

    assert isinstance(history, list)
    assert len(history) > 0
    assert len(history) <= 5

    # Check structure
    snapshot = history[0]
    assert snapshot["symbol"] == "BTC/USD"
    assert "openInterestAmount" in snapshot
    assert "openInterestValue" in snapshot
    assert "timestamp" in snapshot


def test_fetch_open_interests_multiple_symbols(gmx_arbitrum):
    """Test fetch_open_interests for multiple symbols."""
    gmx_arbitrum.load_markets()

    symbols = ["ETH/USD", "BTC/USD"]
    ois = gmx_arbitrum.fetch_open_interests(symbols)

    assert isinstance(ois, dict)

    # Should have data for requested symbols
    for symbol in symbols:
        if symbol in ois:  # Some markets may not have OI data
            assert ois[symbol]["symbol"] == symbol
            assert "openInterestValue" in ois[symbol]


def test_fetch_currencies(gmx_arbitrum):
    """Test fetch_currencies returns token metadata."""
    gmx_arbitrum.load_markets()

    currencies = gmx_arbitrum.fetch_currencies()

    assert isinstance(currencies, dict)
    assert len(currencies) > 0

    # Check structure of a currency
    for code, currency in list(currencies.items())[:3]:  # Check first 3
        assert "id" in currency
        assert "code" in currency
        assert "name" in currency
        assert "info" in currency


def test_timeframes_available(gmx_arbitrum):
    """Test that timeframes are properly exposed."""
    assert hasattr(gmx_arbitrum, "timeframes")
    assert isinstance(gmx_arbitrum.timeframes, dict)

    # Check supported timeframes
    assert "1m" in gmx_arbitrum.timeframes
    assert "5m" in gmx_arbitrum.timeframes
    assert "15m" in gmx_arbitrum.timeframes
    assert "1h" in gmx_arbitrum.timeframes
    assert "4h" in gmx_arbitrum.timeframes
    assert "1d" in gmx_arbitrum.timeframes


def test_inheritance_provides_all_methods(gmx_arbitrum):
    """Verify GMX has all expected CCXT methods from wrapper."""
    # Market data methods
    assert hasattr(gmx_arbitrum, "fetch_markets")
    assert hasattr(gmx_arbitrum, "load_markets")
    assert hasattr(gmx_arbitrum, "fetch_ticker")
    assert hasattr(gmx_arbitrum, "fetch_tickers")
    assert hasattr(gmx_arbitrum, "fetch_ohlcv")
    assert hasattr(gmx_arbitrum, "fetch_trades")

    # Open interest and funding
    assert hasattr(gmx_arbitrum, "fetch_open_interest")
    assert hasattr(gmx_arbitrum, "fetch_open_interest_history")
    assert hasattr(gmx_arbitrum, "fetch_open_interests")
    assert hasattr(gmx_arbitrum, "fetch_funding_rate")
    assert hasattr(gmx_arbitrum, "fetch_funding_rate_history")

    # Utility methods
    assert hasattr(gmx_arbitrum, "fetch_currencies")
    assert hasattr(gmx_arbitrum, "fetch_time")
    assert hasattr(gmx_arbitrum, "fetch_status")

    # Trading methods
    assert hasattr(gmx_arbitrum, "fetch_balance")
    assert hasattr(gmx_arbitrum, "fetch_positions")

    # Helper methods
    assert hasattr(gmx_arbitrum, "parse_ticker")
    assert hasattr(gmx_arbitrum, "parse_ohlcv")
    assert hasattr(gmx_arbitrum, "safe_string")
    assert hasattr(gmx_arbitrum, "safe_integer")
    assert hasattr(gmx_arbitrum, "iso8601")
