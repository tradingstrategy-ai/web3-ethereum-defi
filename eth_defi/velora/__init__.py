"""Velora (formerly ParaSwap) DEX aggregator integration.

Velora is a DEX aggregator that aggregates liquidity across multiple
decentralised exchanges. It provides optimal swap routes and
executes trades atomically.

For more information see `Velora developer documentation <https://developers.velora.xyz>`__.

Key components:

- :py:mod:`eth_defi.velora.constants` - Contract addresses and API configuration
- :py:mod:`eth_defi.velora.api` - API helpers and error handling
- :py:mod:`eth_defi.velora.quote` - Price quoting functionality
- :py:mod:`eth_defi.velora.swap` - Swap transaction building
"""
