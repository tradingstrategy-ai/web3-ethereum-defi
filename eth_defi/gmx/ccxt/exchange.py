from eth_defi.ccxt.exchange_compatible import ExchangeCompatible

from .properties import describe_gmx


class GMX(ExchangeCompatible):
    def describe(self):
        return describe_gmx()
