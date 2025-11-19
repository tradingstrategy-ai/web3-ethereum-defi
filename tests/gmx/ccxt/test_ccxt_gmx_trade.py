"""Tests opening and closing positions using CCXT API."""

from eth_defi.gmx.ccxt.exchange import GMX


def test_open_position_long(ccxt_gmx_arbitrum: GMX):
    """Open long."""
