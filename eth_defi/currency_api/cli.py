"""Command-line entry point for the currency_api exchange rate scanner.

Fetches daily exchange rates for a configurable set of named currencies
(default: EUR, GBP, JPY, AUD, SGD, HKD, TRY, CHF, CAD, BTC, ETH against USD) from the free, no-API-key
fawazahmed0 Exchange API and stores them in a DuckDB database. Resume is
completeness-driven, so re-running only fetches missing dates/currencies.

No authentication is required — all data comes from a public endpoint.

Installed as the ``scan-currencies`` Poetry console script
(``[tool.poetry.scripts]``). Run it with::

    # Full incremental scan (resume from existing DB, default currencies)
    LOG_LEVEL=info poetry run scan-currencies

    # Small test batch — a few days only
    LOG_LEVEL=info START_DATE=2026-06-01 END_DATE=2026-06-05 poetry run scan-currencies

    # Add more currencies (history backfills automatically)
    QUOTE_CURRENCIES=eur,gbp,jpy,aud,sgd,hkd,try,chf,cad,btc,eth,sol poetry run scan-currencies

It can also be run as a module: ``poetry run python -m eth_defi.currency_api.cli``.

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: DuckDB database file. Default: ~/.tradingstrategy/currency-api/exchange-rates.duckdb
- ``BASE_CURRENCY``: Base currency. Default: usd
- ``QUOTE_CURRENCIES``: Comma-separated quote currencies. Default: eur,gbp,jpy,aud,sgd,hkd,try,chf,cad,btc,eth
- ``START_DATE``: ``YYYY-MM-DD`` lower bound. Default: resume / earliest available
- ``END_DATE``: ``YYYY-MM-DD`` upper bound. Default: today (UTC)
- ``MAX_WORKERS``: Parallel date fetchers. Default: 8
- ``REFETCH_TAIL_DAYS``: Recent days to always re-fetch. Default: 3
- ``UNAVAILABLE_GRACE_DAYS``: Age before a 404 is recorded as permanent. Default: 2
- ``MAX_TRANSIENT_ATTEMPTS``: Consecutive transient failures per date before giving up. Default: 5
- ``SOURCE``: Value written to the ``source`` column. Default: fawazahmed0
"""

import datetime
import logging
import os
import sys
from pathlib import Path

from eth_defi.currency_api.constants import (
    CURRENCY_API_DATABASE,
    DEFAULT_BASE_CURRENCY,
    DEFAULT_QUOTE_CURRENCIES,
    SOURCE_NAME,
)
from eth_defi.currency_api.scanner import run_incremental_scan
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def _parse_date(name: str) -> datetime.date | None:
    """Parse an optional ``YYYY-MM-DD`` environment variable into a date."""
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def main() -> None:
    """Run an incremental exchange rate scan from environment configuration.

    Reads the configuration from environment variables (see the module
    docstring), runs :py:func:`~eth_defi.currency_api.scanner.run_incremental_scan`,
    and exits non-zero if any date failed transiently so cron alerting fires.
    """
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/currency-api-scan.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else CURRENCY_API_DATABASE

    base_currency = os.environ.get("BASE_CURRENCY", DEFAULT_BASE_CURRENCY).strip().lower()

    quotes_str = os.environ.get("QUOTE_CURRENCIES", "").strip()
    if quotes_str:
        quote_currencies = tuple(q.strip().lower() for q in quotes_str.split(",") if q.strip())
    else:
        quote_currencies = DEFAULT_QUOTE_CURRENCIES

    start_date = _parse_date("START_DATE")
    end_date = _parse_date("END_DATE")
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    refetch_tail_days = int(os.environ.get("REFETCH_TAIL_DAYS", "3"))
    unavailable_grace_days = int(os.environ.get("UNAVAILABLE_GRACE_DAYS", "2"))
    max_transient_attempts = int(os.environ.get("MAX_TRANSIENT_ATTEMPTS", "5"))
    source = os.environ.get("SOURCE", SOURCE_NAME).strip()

    logger.info("Using log level: %s", default_log_level)
    logger.info("Database path: %s", db_path)
    logger.info("Base currency: %s, quotes: %s", base_currency, ",".join(quote_currencies))

    result = run_incremental_scan(
        db_path=db_path,
        base_currency=base_currency,
        quote_currencies=quote_currencies,
        start_date=start_date,
        end_date=end_date,
        source=source,
        max_workers=max_workers,
        refetch_tail_days=refetch_tail_days,
        unavailable_grace_days=unavailable_grace_days,
        max_transient_attempts=max_transient_attempts,
    )

    db = result.db
    try:
        logger.info(
            "Total rows: %d, date range: %s..%s",
            db.row_count(),
            db.get_min_date(base_currency, source),
            db.get_max_date(base_currency, source),
        )
    finally:
        db.close()

    if result.transient_failures:
        logger.error(
            "%d dates failed transiently; they will be retried on the next run",
            result.transient_failures,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
