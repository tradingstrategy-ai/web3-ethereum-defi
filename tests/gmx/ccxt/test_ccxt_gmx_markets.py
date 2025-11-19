"""Tests for reading available markets and their parameters in CCXT format."""

from eth_defi.gmx.ccxt.exchange import GMX


def test_arbitrum_gmx_fetch_tickers(ccxt_gmx_arbitrum: GMX):
    """Get all markets of GMX in CCXT format"""
    gmx = ccxt_gmx_arbitrum
    tickers = gmx.fetch_tickers()
