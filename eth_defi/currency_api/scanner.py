"""Incremental, gap-aware exchange rate scanner.

Drives :py:func:`~eth_defi.currency_api.client.fetch_rates_for_date` over a date
window, fetching dates in parallel (threaded) and writing to a single shared
DuckDB connection. Resume is **completeness-driven**: it computes the missing
``(date, quote)`` cells from the stored data and the gap table, so transient
holes and newly added quote currencies are always backfilled.

Example::

    from eth_defi.currency_api.scanner import run_incremental_scan
    from eth_defi.currency_api.constants import CURRENCY_API_DATABASE

    result = run_incremental_scan(db_path=CURRENCY_API_DATABASE)
    print(result.db.row_count())
    result.db.close()
"""

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from joblib import Parallel, delayed
from requests import Session
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.client import FetchResult, fetch_rates_for_date
from eth_defi.currency_api.constants import (
    DEFAULT_BASE_CURRENCY,
    DEFAULT_QUOTE_CURRENCIES,
    EARLIEST_AVAILABLE_DATE,
    SOURCE_NAME,
)
from eth_defi.currency_api.database import CurrencyRateDatabase
from eth_defi.currency_api.session import create_currency_api_session

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanResult:
    """Outcome of an incremental scan.

    :ivar db:
        The (still open) database. The caller is responsible for closing it.
    :ivar dates_requested:
        Number of distinct dates fetched over HTTP this run.
    :ivar rows_upserted:
        Number of ``(date, quote)`` rate rows written.
    :ivar quotes_recorded:
        Individually missing quotes recorded as permanently unavailable.
    :ivar quotes_pending:
        Individually missing quotes left pending (within grace, retried later).
    :ivar dates_unavailable:
        Whole dates recorded as permanently unavailable (404 on both hosts).
    :ivar dates_pending:
        Whole dates that 404'd but are within grace (retried later).
    :ivar transient_failures:
        Dates that failed transiently this run and will be retried; the run
        should exit non-zero.
    :ivar dates_given_up:
        Dates that exceeded ``max_transient_attempts`` consecutive transient
        failures and were recorded as permanent ``persistent_error`` gaps.
    """

    #: The still-open database.
    db: CurrencyRateDatabase
    #: Distinct dates fetched over HTTP.
    dates_requested: int = 0
    #: Rate rows written.
    rows_upserted: int = 0
    #: Missing quotes recorded as permanently unavailable.
    quotes_recorded: int = 0
    #: Missing quotes left pending within grace.
    quotes_pending: int = 0
    #: Whole dates recorded as unavailable.
    dates_unavailable: int = 0
    #: Whole dates pending within grace.
    dates_pending: int = 0
    #: Transient failures requiring retry / non-zero exit.
    transient_failures: int = 0
    #: Dates given up on after exceeding the transient-attempt budget.
    dates_given_up: int = 0


def _iter_dates(start: datetime.date, end: datetime.date) -> Iterator[datetime.date]:
    """Yield every calendar date in ``[start, end]`` inclusive."""
    current = start
    while current <= end:
        yield current
        current += datetime.timedelta(days=1)


def run_incremental_scan(
    db_path: Path,
    base_currency: str = DEFAULT_BASE_CURRENCY,
    quote_currencies: tuple[str, ...] = DEFAULT_QUOTE_CURRENCIES,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    source: str = SOURCE_NAME,
    max_workers: int = 8,
    refetch_tail_days: int = 3,
    unavailable_grace_days: int = 2,
    max_transient_attempts: int = 5,
    session: Session | None = None,
) -> ScanResult:
    """Incrementally populate exchange rates into DuckDB.

    Computes the missing ``(date, quote)`` work set from the stored data and the
    gap table, fetches the needed dates in parallel, and upserts the results.
    Permanent gaps are recorded; transient failures are left for retry and
    surfaced in the returned :py:class:`ScanResult`.

    :param db_path:
        DuckDB file path. Created if it does not exist.
    :param base_currency:
        Lower-cased base currency (e.g. ``usd``).
    :param quote_currencies:
        Quote currencies to populate. Adding a currency later backfills its full
        history automatically (no schema change needed).
    :param start_date:
        Lower bound (inclusive). Defaults to ``EARLIEST_AVAILABLE_DATE``;
        clamped so it can never go below it.
    :param end_date:
        Upper bound (inclusive). Defaults to today (UTC).
    :param source:
        Provider identifier written to the ``source`` column.
    :param max_workers:
        Number of threaded date fetchers.
    :param refetch_tail_days:
        Always re-fetch the most recent N days to pick up source corrections.
    :param unavailable_grace_days:
        A 404/missing-quote younger than this many days is treated as
        "not published yet" and retried, not recorded as permanent.
    :param max_transient_attempts:
        Maximum number of consecutive transient failures (non-404 errors such as
        403/5xx/network) tolerated for a single date across runs. Once a date
        reaches this many failures it is given up on — recorded as a permanent
        ``persistent_error`` gap and no longer retried. The counter resets as
        soon as the date succeeds or is confirmed unavailable.
    :param session:
        Optional pre-built session; one is created if omitted.
    :return:
        A :py:class:`ScanResult` carrying the open db and per-run counts.
    """
    base_currency = base_currency.lower()
    quote_currencies = tuple(q.lower() for q in quote_currencies)

    today = native_datetime_utc_now().date()

    floor = start_date or EARLIEST_AVAILABLE_DATE
    if floor < EARLIEST_AVAILABLE_DATE:
        floor = EARLIEST_AVAILABLE_DATE
    end = end_date or today

    db = CurrencyRateDatabase(db_path)
    result = ScanResult(db=db)

    if end < floor:
        logger.warning("Empty scan window: end %s < floor %s", end, floor)
        return result

    # Determine the missing work set at (date, quote) granularity.
    present = db.get_present_pairs(base_currency, source)
    unavailable = db.get_unavailable_pairs(base_currency, source)
    tail_threshold = end - datetime.timedelta(days=refetch_tail_days)

    def date_needs_fetch(d: datetime.date) -> bool:
        # Fetch if any quote is neither present nor a known gap (real missing work).
        if any((d, q) not in present and (d, q) not in unavailable for q in quote_currencies):
            return True
        # All quotes resolved. In the recent tail, re-fetch only dates that actually
        # have data, to capture source corrections — never resurrect a date whose
        # quotes are all recorded gaps (e.g. given-up persistent_error dates).
        if d > tail_threshold:
            return any((d, q) in present for q in quote_currencies)
        return False

    dates_to_fetch = [d for d in _iter_dates(floor, end) if date_needs_fetch(d)]
    result.dates_requested = len(dates_to_fetch)

    if not dates_to_fetch:
        logger.info("Nothing to fetch: %s..%s already complete for %s/%s", floor, end, base_currency, source)
        return result

    logger.info(
        "Fetching %d dates (%s..%s) for base=%s quotes=%s source=%s",
        len(dates_to_fetch),
        dates_to_fetch[0],
        dates_to_fetch[-1],
        base_currency,
        ",".join(quote_currencies),
        source,
    )

    if session is None:
        session = create_currency_api_session(pool_maxsize=max(max_workers, 16))

    # Parallel fetch — workers only do HTTP, no DB writes (thread-safe).
    results: list[FetchResult] = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_rates_for_date)(session, d, base_currency, quote_currencies, source) for d in tqdm(dates_to_fetch, desc="Fetching exchange rates"))

    # Single-threaded write phase on the shared connection.
    prior_attempts = db.get_transient_attempts(base_currency, source)

    for res in results:
        # A gap is recorded as permanent once the date is at least
        # ``unavailable_grace_days`` old; younger gaps are "not published yet".
        is_old = (today - res.date).days >= unavailable_grace_days

        if res.status == "transient_error":
            attempts = prior_attempts.get(res.date, 0) + 1
            if attempts >= max_transient_attempts:
                # Budget exhausted: give up and record a permanent gap so this
                # date is no longer retried (and no longer alerts).
                logger.warning(
                    "Giving up on %s after %d consecutive transient failures; recording persistent_error",
                    res.date,
                    attempts,
                )
                for quote in quote_currencies:
                    db.record_unavailable(res.date, base_currency, quote, source, reason="persistent_error")
                db.clear_transient_attempts(res.date, base_currency, source)
                result.dates_given_up += 1
            else:
                # Still within budget: persist the streak and retry next run.
                db.set_transient_attempts(res.date, base_currency, source, attempts)
                result.transient_failures += 1
            continue

        if res.status == "ok":
            if res.rates and res.rates.rows:
                db.upsert_rates(res.rates)
                result.rows_upserted += len(res.rates.rows)
            for quote in res.missing_quotes:
                if is_old:
                    db.record_unavailable(res.date, base_currency, quote, source, reason="quote_missing")
                    result.quotes_recorded += 1
                else:
                    result.quotes_pending += 1
        else:  # unavailable — whole-date 404 on both hosts
            if is_old:
                for quote in quote_currencies:
                    db.record_unavailable(res.date, base_currency, quote, source, reason="date_404", http_status=404)
                result.dates_unavailable += 1
            else:
                result.dates_pending += 1

        # ok / unavailable are definitive answers for the date — reset any streak.
        db.clear_transient_attempts(res.date, base_currency, source)

    db.save()

    logger.info(
        "Scan complete: requested=%d rows_upserted=%d quotes_recorded=%d quotes_pending=%d dates_unavailable=%d dates_pending=%d transient_failures=%d dates_given_up=%d",
        result.dates_requested,
        result.rows_upserted,
        result.quotes_recorded,
        result.quotes_pending,
        result.dates_unavailable,
        result.dates_pending,
        result.transient_failures,
        result.dates_given_up,
    )

    return result
