Currency API
------------

Historical exchange rate ingestion from the free, no-API-key
`fawazahmed0 Exchange API <https://github.com/fawazahmed0/exchange-api>`__
(``@fawazahmed0/currency-api``).

This module incrementally scans daily historical exchange rates for a
configurable set of named currencies (default: EUR, GBP, JPY, AUD, BTC, ETH against
USD) and stores them in DuckDB:

- One HTTP request per date returns the base currency against ~200 fiat and
  crypto currencies, with a jsDelivr → Cloudflare Pages host fallback
- Completeness-driven incremental resume (backfills holes and newly added
  currencies; tracks permanently missing cells)
- A ``source`` column so additional rate sources can be added later behind the
  same schema

No authentication is required -- all data comes from a public endpoint.

For architecture details, the DuckDB schema, environment variables and the
incremental scanning model, see
`README-currency-api.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/currency_api/README-currency-api.md>`__.

.. autosummary::
   :toctree: _autosummary_currency_api
   :recursive:

   eth_defi.currency_api.scanner
   eth_defi.currency_api.client
   eth_defi.currency_api.database
   eth_defi.currency_api.session
   eth_defi.currency_api.constants
