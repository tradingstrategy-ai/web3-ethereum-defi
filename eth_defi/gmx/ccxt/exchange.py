from eth_defi.ccxt.exchange_compatible import ExchangeCompatible

from .properties import describe_gmx

from eth_defi.gmx.api import GMXAPI


class GMX(ExchangeCompatible):
    """Wrap internal GMX API into CCXT-compatible exchange class."""

    def __init__(self, api: GMXAPI | None):
        self.api = api

    def describe(self):
        """Get CCXT exchange description."""
        return describe_gmx()

    def fetch_markets(self, params=None):
        """Fetch markets from GMX exchange."""
        return self.api.get_tickers()
