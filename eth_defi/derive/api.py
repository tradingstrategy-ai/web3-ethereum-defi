"""Derive.xyz public API functions.

Typed wrappers for public (unauthenticated) Derive API endpoints.

Uses :py:func:`~eth_defi.derive.session.create_derive_session` for
HTTP connections with rate limiting and retry logic.

Example::

    from eth_defi.derive.api import fetch_perpetual_instruments, fetch_funding_rate_history
    from eth_defi.derive.session import create_derive_session

    session = create_derive_session()

    # Discover all active perpetual instruments
    instruments = fetch_perpetual_instruments(session)
    print(instruments)  # ['ETH-PERP', 'BTC-PERP', ...]

    # Fetch funding rate history for one instrument
    rates = fetch_funding_rate_history(session, "ETH-PERP")
    for r in rates:
        print(f"{r.timestamp}: rate={r.funding_rate}")
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from requests import Session

from eth_defi.derive.constants import DERIVE_MAINNET_API_URL

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FundingRateEntry:
    """A single funding rate snapshot from Derive.

    Represents one hourly funding rate observation for a perpetual
    instrument.
    """

    #: Instrument name (e.g. ``"ETH-PERP"``)
    instrument: str

    #: Snapshot timestamp (naive UTC)
    timestamp: datetime.datetime

    #: Timestamp in milliseconds since epoch
    timestamp_ms: int

    #: Hourly funding rate as a decimal fraction
    #: (e.g. ``Decimal("0.00001234")``)
    funding_rate: Decimal


def _unwrap_result(data: dict, method: str) -> dict:
    """Unwrap JSON-RPC envelope and raise on error.

    :param data:
        Parsed JSON response.
    :param method:
        API method name for error messages.
    :return:
        The ``result`` field from the response.
    :raises ValueError:
        If the response contains an error.
    """
    if "error" in data:
        error = data["error"]
        error_data = error.get("data", "")
        error_msg = f"Derive API error for {method}: {error.get('code', 'unknown')}: {error.get('message', 'no message')}"
        if error_data:
            error_msg += f" (data: {error_data})"
        raise ValueError(error_msg)
    return data.get("result", {})


def fetch_perpetual_instruments(
    session: Session,
    currency: str | None = None,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> list[str]:
    """Fetch all active perpetual instrument names from Derive.

    Calls the public ``get_all_instruments`` endpoint with
    ``instrument_type="perp"`` to discover available perpetual
    contracts.

    Example::

        from eth_defi.derive.api import fetch_perpetual_instruments
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        instruments = fetch_perpetual_instruments(session)
        # ['ETH-PERP', 'BTC-PERP', 'SOL-PERP', ...]

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param currency:
        Optional currency filter (e.g. ``"ETH"``, ``"BTC"``).
        If ``None``, returns all active perps.
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Sorted list of instrument names.
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/get_all_instruments"
    instruments = []
    page = 1

    while True:
        params = {
            "instrument_type": "perp",
            "expired": False,
            "page": page,
            "page_size": 1000,
        }
        if currency is not None:
            params["currency"] = currency

        response = session.post(
            url,
            json=params,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()

        result = _unwrap_result(response.json(), "get_all_instruments")
        page_instruments = result.get("instruments", [])

        for inst in page_instruments:
            name = inst.get("instrument_name")
            if name and inst.get("is_active", True):
                instruments.append(name)

        pagination = result.get("pagination", {})
        num_pages = pagination.get("num_pages", 1)
        if page >= num_pages:
            break
        page += 1

    instruments.sort()
    logger.info("Found %d active perpetual instruments", len(instruments))
    return instruments


def fetch_funding_rate_history(
    session: Session,
    instrument_name: str,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    base_url: str = DERIVE_MAINNET_API_URL,
    timeout: float = 30.0,
) -> list[FundingRateEntry]:
    """Fetch funding rate history for a Derive perpetual instrument.

    Calls the public ``get_funding_rate_history`` endpoint.
    No authentication required.

    The API restricts ``start_time`` to at most 30 days in the past.
    For accumulating historical data beyond 30 days, see
    :py:class:`~eth_defi.derive.historical.DeriveFundingRateDatabase`
    which handles resumable syncs.

    Data is returned at hourly resolution — the native funding rate
    interval on Derive.

    Example::

        from eth_defi.derive.api import fetch_funding_rate_history
        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        rates = fetch_funding_rate_history(session, "ETH-PERP")
        for r in rates:
            print(f"{r.timestamp}: {r.funding_rate}")

    :param session:
        HTTP session from :py:func:`~eth_defi.derive.session.create_derive_session`.
    :param instrument_name:
        Perpetual instrument name (e.g. ``"ETH-PERP"``).
    :param start_time:
        Start of the query window (naive UTC). Defaults to 30 days ago.
        API rejects values older than 30 days from now.
    :param end_time:
        End of the query window (naive UTC). Defaults to now.
    :param base_url:
        Derive API base URL.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of funding rate entries sorted by timestamp ascending.
    :raises ValueError:
        If the API returns an error response.
    """
    url = f"{base_url}/public/get_funding_rate_history"

    params: dict = {"instrument_name": instrument_name}

    if start_time is not None:
        params["start_time"] = int(start_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    if end_time is not None:
        params["end_time"] = int(end_time.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    response = session.post(
        url,
        json=params,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()

    result = _unwrap_result(response.json(), "get_funding_rate_history")
    history = result.get("funding_rate_history", [])

    entries = []
    for item in history:
        ts_ms = int(item["timestamp"])
        ts_dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc).replace(tzinfo=None)
        entries.append(
            FundingRateEntry(
                instrument=instrument_name,
                timestamp=ts_dt,
                timestamp_ms=ts_ms,
                funding_rate=Decimal(str(item["funding_rate"])),
            )
        )

    entries.sort(key=lambda e: e.timestamp_ms)
    logger.info("Fetched %d funding rate entries for %s", len(entries), instrument_name)
    return entries
