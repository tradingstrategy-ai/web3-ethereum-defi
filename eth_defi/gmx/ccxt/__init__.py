"""CCXT-compatible exchange adapter for GMX protocol."""

from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.ccxt.exchange import GMX

__all__ = ["GMX", "InsufficientHistoricalDataError"]
