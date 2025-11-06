from eth_defi.gmx.ccxt.exchange import GMX


def test_init_ccxt_gmx():
    """Smoke test all funky CCXT imports do not crash."""
    exchange = GMX()
    assert isinstance(exchange, GMX)
