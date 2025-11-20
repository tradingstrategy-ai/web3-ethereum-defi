from eth_defi.gmx.ccxt.exchange import GMX


def test_init_ccxt_gmx():
    """Smoke test all funky CCXT imports do not crash."""
    exchange = GMX(config=None)
    assert isinstance(exchange, GMX)


def test_describe_ccxt_gmx():
    """See what features we are supporting and they are correctly marked supported.

    - Go through CCXT feature matrix
    - This will be read by FreqTrade and other libraries when used with CCXT adapters
    """
    exchange = GMX(config=None)
    description = exchange.describe()
    has = description["has"]

    # Check features that are actually marked as True
    assert has["fetchTicker"] is True
    assert has["fetchTrades"] is True
    assert has["publicAPI"] is True
    assert has["privateAPI"] is True
