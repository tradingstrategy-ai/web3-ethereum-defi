"""WisdomTree DataSpan NAV API client.

WisdomTree documents the endpoint at
https://docs.wisdomtreeconnect.com/dataspan/nav . The API key is intentionally
supplied by the operator instead of embedding browser credentials in scans.
"""

import datetime
import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

WISDOMTREE_DATASPAN_NAV_URL = "https://dataspanapi.wisdomtree.com/funddetails/nav/"
WISDOMTREE_DATASPAN_API_KEY_ENV = "WISDOMTREE_DATASPAN_API_KEY"


class WisdomTreeAPIError(RuntimeError):
    """Raised when WisdomTree's documented NAV endpoint cannot be read."""


@dataclass(slots=True, frozen=True)
class WisdomTreeNAVPoint:
    """One official NAV observation."""

    timestamp: datetime.datetime
    nav: Decimal


def _parse_timestamp(value: str) -> datetime.datetime:
    """Parse a DataSpan date into a naive UTC timestamp."""

    value = value.replace("Z", "+00:00")
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        timestamp = datetime.datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=datetime.UTC)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(datetime.UTC).replace(tzinfo=None)
    return timestamp


def _extract_records(payload: object) -> list[dict[str, Any]]:
    """Normalise documented DataSpan list and wrapper response formats."""

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "history", "results", "navHistory"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]
    raise WisdomTreeAPIError(f"Unexpected WisdomTree NAV response type: {type(payload)!r}")


def fetch_wisdomtree_nav_history(
    ticker: str,
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    api_url: str = WISDOMTREE_DATASPAN_NAV_URL,
) -> Iterator[WisdomTreeNAVPoint]:
    """Fetch official current and historical NAV observations.

    The request uses WisdomTree's documented ``history=True`` query. It only
    accepts explicit NAV/date fields and fails closed when the provider changes
    its response shape, so an unknown response can never silently become a
    one-dollar estimate.

    :param ticker: WisdomTree fund ticker, e.g. ``WTGXX``.
    :param api_key: DataSpan key or ``WISDOMTREE_DATASPAN_API_KEY``.
    :param session: Optional requests session for tests and connection reuse.
    :param api_url: Endpoint override for tests.
    :return: Chronologically sorted official NAV observations.
    :raise WisdomTreeAPIError: If credentials, HTTP response or fields are invalid.
    """

    api_key = api_key or os.environ.get(WISDOMTREE_DATASPAN_API_KEY_ENV)
    if not api_key:
        raise WisdomTreeAPIError(f"Set {WISDOMTREE_DATASPAN_API_KEY_ENV} to read WisdomTree NAV history")
    client = session or requests.Session()
    response = client.get(
        api_url,
        params={"ticker": ticker, "history": "true"},
        headers={"x-wt-dataspan-key": api_key},
        timeout=30,
    )
    try:
        response.raise_for_status()
        records = _extract_records(response.json())
    except (requests.RequestException, ValueError) as error:
        raise WisdomTreeAPIError(f"Could not fetch WisdomTree NAV for {ticker}: {error}") from error

    points: list[WisdomTreeNAVPoint] = []
    for record in records:
        date = next((record.get(key) for key in ("asOfDate", "date", "asOf", "navDate") if record.get(key)), None)
        nav = next((record.get(key) for key in ("nav", "netAssetValue", "value") if record.get(key) is not None), None)
        if not isinstance(date, str) or nav is None:
            continue
        try:
            points.append(WisdomTreeNAVPoint(timestamp=_parse_timestamp(date), nav=Decimal(str(nav))))
        except (ValueError, ArithmeticError) as error:
            raise WisdomTreeAPIError(f"Invalid WisdomTree NAV row for {ticker}: {record!r}") from error
    if not points:
        raise WisdomTreeAPIError(f"No usable WisdomTree NAV observations returned for {ticker}")
    yield from sorted(points, key=lambda point: point.timestamp)
