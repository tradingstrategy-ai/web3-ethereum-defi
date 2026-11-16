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


def test_gmx_initialization_with_config(arbitrum_fork_config):
    """Verify GMX can be initialized with GMXConfig."""
    gmx = GMX(config=arbitrum_fork_config)

    assert gmx.config is not None
    assert gmx.web3 is not None
    assert gmx.subsquid is not None
    assert isinstance(gmx.markets, dict)
    assert isinstance(gmx.timeframes, dict)
