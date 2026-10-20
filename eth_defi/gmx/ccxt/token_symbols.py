"""Resolve the GMX token symbol for a CCXT market.

The CCXT market id is not always a GMX token symbol. Synthetic markets, where
the long and short token share an address, are exposed with a ``2`` suffix
(``HYPE2``, ``XRP2``, ``ZEC2``, ``NEAR2``), and other markets may carry a ``k``
prefix. The GMX price endpoints (``/prices/candles``, ``/prices/tickers``) only
know the underlying token (``HYPE``), so a market is resolved back to its index
token and that address is mapped through ``/tokens``. Without this, every price
request for such a market fails with HTTP 400 on all four API hosts.
"""

from typing import Any


def token_symbols_by_address(tokens_payload: dict[str, Any]) -> dict[str, str]:
    """Build a case-insensitive token address to symbol map from ``/tokens``.

    :param tokens_payload:
        Raw ``/tokens`` response, ``{"tokens": [{"address": ..., "symbol": ...}]}``.

    :return:
        Lower-cased token address mapped to its GMX token symbol.
    """
    return {str(entry["address"]).lower(): str(entry["symbol"]) for entry in tokens_payload.get("tokens") or [] if entry.get("address") and entry.get("symbol")}


def resolve_market_token_symbol(market_info: dict[str, Any], symbols_by_address: dict[str, str]) -> str:
    """Return the GMX token symbol for a market.

    Prefers the market's index token address, which is shared by a synthetic
    market and its canonical counterpart, so ``HYPE2`` resolves to ``HYPE``.
    Falls back to the market id when the address is unknown or absent.

    :param market_info:
        CCXT market dict as produced by :meth:`GMX.load_markets`.
    :param symbols_by_address:
        Map returned by :func:`token_symbols_by_address`.

    :return:
        GMX token symbol for the price request.
    """
    index_token = (market_info.get("info") or {}).get("index_token")
    if index_token:
        symbol = symbols_by_address.get(str(index_token).lower())
        if symbol:
            return symbol
    return str(market_info["id"])
