"""Test GMX CCXT inheritance structure and MRO."""

import ccxt

from eth_defi.ccxt.exchange_compatible import ExchangeCompatible
from eth_defi.gmx.ccxt.exchange import GMX


def test_gmx_inheritance_structure(ccxt_gmx_arbitrum):
    """Verify GMX properly inherits from ExchangeCompatible."""
    gmx = ccxt_gmx_arbitrum

    # Should inherit from ExchangeCompatible
    assert isinstance(gmx, ExchangeCompatible)

    # Should have access to ccxt.Exchange base
    assert isinstance(gmx, ccxt.Exchange)


def test_gmx_method_resolution_order():
    """Verify methods resolve in correct order (MRO)."""
    # Check MRO order
    mro = GMX.__mro__

    assert mro[0] == GMX
    assert mro[1] == ExchangeCompatible
    # ccxt.Exchange should appear after ExchangeCompatible
    assert ccxt.Exchange in mro


def test_gmx_has_describe_method(ccxt_gmx_arbitrum):
    """Verify describe() method is accessible and returns proper structure."""
    gmx = ccxt_gmx_arbitrum

    # Method should exist
    assert hasattr(gmx, "describe")
    assert callable(gmx.describe)

    # Should return dict with exchange info
    description = gmx.describe()
    assert isinstance(description, dict)
    assert "id" in description
    assert description["id"] == "gmx"


def test_gmx_has_ccxt_methods(ccxt_gmx_arbitrum):
    """Verify GMX has access to all CCXT methods."""
    gmx = ccxt_gmx_arbitrum

    # Core market data methods
    assert hasattr(gmx, "fetch_markets")
    assert hasattr(gmx, "fetch_ticker")
    assert hasattr(gmx, "fetch_tickers")
    assert hasattr(gmx, "fetch_ohlcv")
    assert hasattr(gmx, "fetch_trades")

    # Open interest and funding
    assert hasattr(gmx, "fetch_open_interest")
    assert hasattr(gmx, "fetch_open_interest_history")
    assert hasattr(gmx, "fetch_funding_rate")
    assert hasattr(gmx, "fetch_funding_rate_history")

    # Trading methods
    assert hasattr(gmx, "fetch_balance")
    assert hasattr(gmx, "fetch_positions")

    # Utility methods
    assert hasattr(gmx, "load_markets")
    assert hasattr(gmx, "fetch_currencies")
    assert hasattr(gmx, "fetch_time")
    assert hasattr(gmx, "fetch_status")


def test_gmx_initialization_with_config(arbitrum_fork_config):
    """Verify GMX can be initialized with GMXConfig."""
    gmx = GMX(config=arbitrum_fork_config)

    assert gmx.config is not None
    assert gmx.web3 is not None
    assert gmx.subsquid is not None
    assert isinstance(gmx.markets, dict)
    assert isinstance(gmx.timeframes, dict)


def test_gmx_attributes(ccxt_gmx_arbitrum):
    """Verify GMX class properly initialized all attributes."""
    gmx = ccxt_gmx_arbitrum

    # GMX class attributes
    assert hasattr(gmx, "config")
    assert hasattr(gmx, "api")
    assert hasattr(gmx, "web3")
    assert hasattr(gmx, "subsquid")
    assert hasattr(gmx, "markets")
    assert hasattr(gmx, "timeframes")
    assert hasattr(gmx, "leverage")
    assert hasattr(gmx, "_token_metadata")
