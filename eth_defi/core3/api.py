"""Fetch helpers for Core3 Projects Data API endpoints.

Thin wrappers around individual API endpoints that handle URL
construction, response unwrapping, and error propagation. Each
function takes a :py:class:`~eth_defi.core3.session.Core3Session`
and returns the parsed JSON response.

See `Core3 API documentation <https://docs.core3.io/projects-data-api>`__
for endpoint details.
"""

import logging

from eth_defi.core3.constants import CORE3_DEFAULT_TIMEOUT
from eth_defi.core3.session import Core3Session

logger = logging.getLogger(__name__)


def fetch_project_list(session: Core3Session, timeout: float = CORE3_DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch the full project list from ``/v1/list``.

    Returns the unwrapped list (the API wraps it as ``{"list": [...]}``)
    containing slug, name, coingecko_id, and PoL score per project.

    :param session:
        Core3 API session.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of project dicts.
    """
    url = f"{session.api_url}/v1/list"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["list"]


def fetch_project_detail(session: Core3Session, slug: str, timeout: float = CORE3_DEFAULT_TIMEOUT) -> dict:
    """Fetch full project detail from ``/v1/{slug}``.

    Returns the top-level project object including description, rank,
    PoL score, market cap, links, top_risks, and seals.

    :param session:
        Core3 API session.
    :param slug:
        Project identifier (e.g. ``'ethereum'``, ``'aave'``).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Project detail dict (raw JSON response).
    """
    url = f"{session.api_url}/v1/{slug}"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_pol_history(session: Core3Session, slug: str, timeout: float = CORE3_DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch all-time PoL history chart from ``/v1/{slug}/pol/history/chart``.

    Returns a list of ``{score, timestamp}`` points, unwrapped from
    ``{"points": [...]}``. Used for initial backfill.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/{slug}/pol/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_history_incremental(
    session: Core3Session,
    slug: str,
    from_ts: int,
    to_ts: int,
    timeout: float = CORE3_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch ranged PoL history from ``/v1/{slug}/pol/history``.

    Uses the ``from`` and ``to`` query parameters (unix timestamps in
    seconds) to fetch only the requested range. Used for incremental
    updates after the initial backfill.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/{slug}/pol/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_category_history(session: Core3Session, slug: str, timeout: float = CORE3_DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch all-time PoL category breakdown history from ``/v1/{slug}/pol/by_category/history/chart``.

    Returns a list of points, each containing a timestamp and per-category
    PoL scores (security, financial, operational, reputational, regulatory).

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with category score breakdowns.
    """
    url = f"{session.api_url}/v1/{slug}/pol/by_category/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_category_history_incremental(
    session: Core3Session,
    slug: str,
    from_ts: int,
    to_ts: int,
    timeout: float = CORE3_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch ranged PoL category breakdown history from ``/v1/{slug}/pol/by_category/history``.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with category score breakdowns.
    """
    url = f"{session.api_url}/v1/{slug}/pol/by_category/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_index_pol_history(session: Core3Session, timeout: float = CORE3_DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch all-time index-level aggregate PoL history from ``/v1/pol/history/chart``.

    :param session:
        Core3 API session.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/pol/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_index_pol_history_incremental(
    session: Core3Session,
    from_ts: int,
    to_ts: int,
    timeout: float = CORE3_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch ranged index-level aggregate PoL history from ``/v1/pol/history``.

    :param session:
        Core3 API session.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/pol/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_section_detail(session: Core3Session, slug: str, section: str, timeout: float = CORE3_DEFAULT_TIMEOUT) -> dict:
    """Fetch a project section endpoint (security, financial, etc.).

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param section:
        Section name: ``'security'``, ``'financial'``, ``'operational'``,
        ``'reputational'``, or ``'regulatory'``.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Section detail dict (raw JSON response).
    """
    url = f"{session.api_url}/v1/{slug}/{section}"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
