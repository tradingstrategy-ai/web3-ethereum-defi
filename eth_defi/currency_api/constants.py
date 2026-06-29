"""Constants for the fawazahmed0 Exchange API integration.

Canonical API documentation: https://github.com/fawazahmed0/exchange-api
"""

import datetime
from pathlib import Path

#: Primary host (jsDelivr CDN) URL template.
#:
#: ``{date}`` is ``latest`` or an ISO ``YYYY-MM-DD`` date; ``{base}`` is the
#: lower-cased base currency (e.g. ``usd``). One request returns the base
#: currency against ~200 fiat and crypto currencies.
JSDELIVR_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{base}.min.json"

#: Fallback host (Cloudflare Pages) URL template.
#:
#: Used when jsDelivr returns 404 or a transient error. The upstream docs
#: recommend always having this fallback configured.
PAGES_DEV_URL_TEMPLATE = "https://{date}.currency-api.pages.dev/v1/currencies/{base}.min.json"

#: Value written to the ``source`` column for rows fetched from this provider.
#:
#: Future rate sources get their own string (e.g. ``frankfurter``, ``coingecko``).
SOURCE_NAME = "fawazahmed0"

#: Default base currency. All rates are stored as "units of quote per 1 base".
DEFAULT_BASE_CURRENCY = "usd"

#: Default set of named quote currencies to scan.
DEFAULT_QUOTE_CURRENCIES = ("eur", "gbp", "jpy", "aud", "btc", "eth")

#: Earliest date for which the source publishes data.
#:
#: Verified via jsDelivr/npm version probing: the earliest dated package
#: version is ``2024.3.2``; dates before this return HTTP 404. Adjust here if the
#: provider backfills older history.
EARLIEST_AVAILABLE_DATE = datetime.date(2024, 3, 2)

#: Default DuckDB database path.
CURRENCY_API_DATABASE = Path("~/.tradingstrategy/currency-api/exchange-rates.duckdb").expanduser()
