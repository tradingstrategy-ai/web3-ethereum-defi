"""CCXT-compatible adapter base for DEXes and perp DEXes."""

from typing import Any

from ccxt.base.exchange import Exchange


class ExchangeCompatible(Exchange):
    """CCXT Exchange class compatible adapter for DEX.

    - `View CCXT documentation for the overview of methods <https://docs.ccxt.com/#/>`__
    - See `CCXT exchange bae class <https://github.com/ccxt/ccxt/blob/master/python/ccxt/base/exchange.py>`__
    """

    def describe(self) -> Any:
        raise NotImplementedError()
