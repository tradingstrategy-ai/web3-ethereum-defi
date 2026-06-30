"""HTTP client for the fawazahmed0 Exchange API.

Fetches the daily exchange rate document for a single date and base currency,
with jsDelivr → pages.dev host fallback, and classifies the outcome so the
scanner can distinguish "permanently missing" from "retry later".

Rate direction: the API returns ``base[quote]`` = units of ``quote`` per **1
unit of base** (e.g. for base ``usd``, ``usd["eur"] = 0.878`` means 1 USD =
0.878 EUR). We keep this raw value; USD-per-unit is the inverse ``1 / rate``.

Canonical API documentation: https://github.com/fawazahmed0/exchange-api
"""

import datetime
import logging
from dataclasses import dataclass, field
from typing import Literal

import requests
from requests import Session

from eth_defi.currency_api.constants import JSDELIVR_URL_TEMPLATE, PAGES_DEV_URL_TEMPLATE

logger = logging.getLogger(__name__)

#: Outcome of a single date fetch.
#:
#: - ``ok``: the date document was retrieved (some requested quotes may still be
#:   absent — see :py:attr:`FetchResult.missing_quotes`).
#: - ``unavailable``: HTTP 404 on **both** hosts — the whole date has no data.
#: - ``transient_error``: network error / 5xx / malformed body — must be retried.
FetchStatus = Literal["ok", "unavailable", "transient_error"]

#: Default per-request timeout in seconds.
DEFAULT_TIMEOUT = 30.0


@dataclass(slots=True)
class DateRates:
    """Parsed exchange rates for one date and base currency.

    :ivar date:
        UTC calendar date of the quotes.
    :ivar base_currency:
        Lower-cased base currency code (e.g. ``usd``).
    :ivar source:
        Provider identifier written to the ``source`` column.
    :ivar rows:
        List of ``(quote_currency, rate)`` tuples where ``rate`` is units of the
        quote currency per 1 unit of the base currency (raw API value).
    """

    #: UTC calendar date of the quotes.
    date: datetime.date
    #: Lower-cased base currency code.
    base_currency: str
    #: Provider identifier.
    source: str
    #: ``(quote_currency, rate)`` tuples, raw API values.
    rows: list[tuple[str, float]]


@dataclass(slots=True)
class FetchResult:
    """Classified result of fetching one date.

    :ivar date:
        The date that was requested.
    :ivar status:
        See :py:data:`FetchStatus`.
    :ivar rates:
        Parsed rates for the present quotes, or ``None`` when not ``ok``.
    :ivar missing_quotes:
        Requested quote currencies that were absent from an otherwise-200 body.
        Never fabricated — handed back so the scanner can grace/record them.
    """

    #: The date that was requested.
    date: datetime.date
    #: Outcome classification.
    status: FetchStatus
    #: Parsed rates for present quotes, or ``None``.
    rates: DateRates | None = None
    #: Requested quotes absent from a 200 body.
    missing_quotes: tuple[str, ...] = field(default_factory=tuple)


def _attempt(session: Session, url: str, timeout: float) -> tuple[str, dict | None]:
    """Make a single GET attempt and classify the HTTP outcome.

    :param session:
        Shared requests session (carries retry policy).
    :param url:
        Fully-formed URL to fetch.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        ``("ok", json_dict)`` on 200, ``("404", None)`` on a definitive 404,
        or ``("transient", None)`` on a network error / non-200 / non-404 /
        unparseable body.
    """
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        # Connection reset, DNS failure, timeout, etc. — retry later.
        logger.debug("Request to %s failed transiently: %s", url, e)
        return "transient", None

    if resp.status_code == 200:
        try:
            return "ok", resp.json()
        except ValueError as e:
            # 200 but body is not JSON — treat as transient, do not advance.
            logger.warning("Malformed JSON from %s: %s", url, e)
            return "transient", None

    if resp.status_code == 404:
        return "404", None

    # 5xx is already retried by the adapter; anything else here is transient.
    logger.debug("Unexpected status %d from %s", resp.status_code, url)
    return "transient", None


def fetch_rates_for_date(
    session: Session,
    date: datetime.date,
    base_currency: str,
    quote_currencies: tuple[str, ...],
    source: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> FetchResult:
    """Fetch and classify the exchange rate document for a single date.

    Tries the jsDelivr host first, falling back to the pages.dev host on a 404
    or transient failure. A date is only declared :py:data:`unavailable` when
    **both** hosts return a definitive 404.

    :param session:
        Shared requests session from
        :py:func:`~eth_defi.currency_api.session.create_currency_api_session`.
    :param date:
        UTC calendar date to fetch.
    :param base_currency:
        Lower-cased base currency code; interpolated into the URL (never
        hardcoded ``usd``).
    :param quote_currencies:
        Quote currency codes to extract from the document.
    :param source:
        Provider identifier stored on the returned :py:class:`DateRates`.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        A :py:class:`FetchResult` classifying the outcome.
    """
    iso = date.isoformat()
    base = base_currency.lower()

    primary_url = JSDELIVR_URL_TEMPLATE.format(date=iso, base=base)
    primary_status, data = _attempt(session, primary_url, timeout)

    if primary_status != "ok":
        # Try the Cloudflare Pages fallback on either a 404 or a transient error.
        fallback_url = PAGES_DEV_URL_TEMPLATE.format(date=iso, base=base)
        fallback_status, fallback_data = _attempt(session, fallback_url, timeout)

        if fallback_status == "ok":
            data = fallback_data
        elif primary_status == "404" and fallback_status == "404":
            # Both hosts agree the date does not exist.
            return FetchResult(date=date, status="unavailable")
        else:
            # Mixed / transient — cannot confirm absence, retry next run.
            return FetchResult(date=date, status="transient_error")

    return _parse_document(date, base, quote_currencies, source, data)


def _parse_document(
    date: datetime.date,
    base_currency: str,
    quote_currencies: tuple[str, ...],
    source: str,
    data: dict | None,
) -> FetchResult:
    """Extract the requested quotes from a 200 response body.

    :param date:
        Date being parsed.
    :param base_currency:
        Lower-cased base currency code; the document nests rates under this key.
    :param quote_currencies:
        Quote codes to extract.
    :param source:
        Provider identifier.
    :param data:
        Decoded JSON body, expected shape ``{"date": ..., "<base>": {...}}``.
    :return:
        ``ok`` result with the present quotes (and any ``missing_quotes``) when the
        document is well-formed — even if no requested quote is present, since a
        valid document means the date IS published and *absent* quotes are permanent
        per-quote gaps, not a transient failure. Returns ``transient_error`` when the
        body is structurally malformed (missing the base key) **or** a requested
        quote is present but its value is unparseable: corrupt data must be retried,
        not stored partially or recorded as a permanent ``quote_missing`` gap.
    """
    base_map = data.get(base_currency) if isinstance(data, dict) else None
    if not isinstance(base_map, dict):
        # Document missing the base key entirely — malformed, retry later.
        logger.warning("Document for %s missing base key %r", date, base_currency)
        return FetchResult(date=date, status="transient_error")

    rows: list[tuple[str, float]] = []
    missing: list[str] = []
    malformed: list[str] = []
    for quote in quote_currencies:
        value = base_map.get(quote)
        if value is None:
            # Quote genuinely absent — never fabricate; a permanent per-quote gap.
            missing.append(quote)
            continue
        try:
            rows.append((quote, float(value)))
        except (TypeError, ValueError):
            # Present but unparseable — corrupt source data, distinct from "absent".
            logger.warning("Malformed rate %r for %s/%s on %s", value, base_currency, quote, date)
            malformed.append(quote)

    if malformed:
        # A corrupt value means the document is suspect: retry the whole date rather
        # than storing partial data or permanently recording the cell as missing.
        return FetchResult(date=date, status="transient_error", missing_quotes=tuple(missing))

    # A well-formed document with zero present quotes is still ``ok``: the date
    # exists, so the absent quotes are recorded as permanent gaps after grace
    # (not retried forever as a transient failure).
    if not rows:
        logger.warning("No requested quotes present for %s (missing %s)", date, missing)

    rates = DateRates(date=date, base_currency=base_currency, source=source, rows=rows)
    return FetchResult(date=date, status="ok", rates=rates, missing_quotes=tuple(missing))
