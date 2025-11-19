from eth_defi.gmx.ccxt.exchange import GMX


def test_init_ccxt_gmx():
    """Smoke test all funky CCXT imports do not crash."""
    exchange = GMX(api=None)
    assert isinstance(exchange, GMX)


def test_describe_ccxt_gmx():
    """See what features we are supporting and they are correctly marked supported.

    - Go through CCXT feature matrix
    - This will be read by FreqTrade and other libraries when used with CCXT adapters
    """
    exchange = GMX(api=None)
    description = exchange.describe()
    has = description["has"]
    assert has["fetchTickers"] is True
    assert has["fetchOHLCV"] is True
    # etc.
