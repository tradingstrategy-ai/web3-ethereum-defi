"""Historical exchange rate ingestion from the fawazahmed0 Exchange API.

This package incrementally scans daily historical exchange rates for a
configurable set of named currencies (default: EUR, GBP, JPY, AUD, BTC, ETH against
USD) and stores them in a DuckDB database.

The data is sourced from the free, no-API-key
`fawazahmed0 Exchange API <https://github.com/fawazahmed0/exchange-api>`__
(``@fawazahmed0/currency-api``). A single HTTP request per date returns the base
currency against ~200 fiat and crypto currencies, so all named quote currencies
come from one endpoint.

The DuckDB schema carries a ``source`` column so additional rate sources (e.g.
ECB/Frankfurter, CoinGecko, on-chain oracles) can be added later without
disturbing existing rows.

See :py:func:`eth_defi.currency_api.scanner.run_incremental_scan` for the main
entry point and ``scripts/currency_api/README-currency-api.md`` for operator
documentation.
"""
