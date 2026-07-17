"""Normalise signed Chronicle Proof of Asset history for Securitize funds.

Chronicle's public STAC dashboard is a visualisation of signed, off-chain
Proof of Asset records. Unlike RedStone, it does not currently document a
stable public history endpoint. This module deliberately accepts an explicit
operator-provided JSON URL instead of guessing an undocumented route.

The parser supports the dashboard's compact ``timestamp``, ``nav`` and
``totalValueLocked`` record shape. A configured source can therefore use the
same in-memory Securitize merger as RedStone without a second Parquet rewrite.

Reference: https://chroniclelabs.org/dashboard/proofofasset/securitize-stac
"""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from eth_typing import HexAddress

from eth_defi.tokenised_fund.securitize.description import STAC_ETHEREUM

#: Timeout for a public Chronicle history request in seconds.
DEFAULT_CHRONICLE_API_TIMEOUT = 20.0

#: Unix seconds above this are interpreted as milliseconds.
UNIX_MILLISECONDS_THRESHOLD = 10_000_000_000


class ChronicleAPIError(RuntimeError):
    """Raised when a configured Chronicle history source is malformed."""


@dataclass(slots=True, frozen=True)
class ChronicleSecuritizeFeed:
    """One Securitize product verified by Chronicle Proof of Asset."""

    #: EVM chain hosting the Securitize DSToken.
    chain_id: int

    #: Lower-case DSToken address.
    token: HexAddress

    #: Chronicle public dashboard slug.
    dashboard_slug: str


@dataclass(slots=True, frozen=True)
class ChroniclePricePoint:
    """One Chronicle-verified NAV and optional total-value observation."""

    #: UTC timestamp of the signed observation.
    timestamp: datetime.datetime

    #: Verified fund NAV per share in USD.
    share_price: Decimal

    #: Optional verified total fund value in USD.
    total_assets: Decimal | None


#: Securitize products currently announced as Chronicle Proof of Asset feeds.
CHRONICLE_SECURITIZE_FEEDS: dict[tuple[int, HexAddress], ChronicleSecuritizeFeed] = {
    (STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token): ChronicleSecuritizeFeed(STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token, "securitize-stac"),
}


def _as_decimal(value: object, field_name: str) -> Decimal:
    """Parse a positive Chronicle numeric field.

    :param value:
        Raw JSON numeric field.
    :param field_name:
        Field name used in the diagnostic error.
    :return:
        Positive decimal value.
    :raise ChronicleAPIError:
        If the value is absent, malformed or non-positive.
    """

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ChronicleAPIError(f"Chronicle record has an invalid {field_name}") from error
    if decimal_value <= 0:
        raise ChronicleAPIError(f"Chronicle record has a non-positive {field_name}")
    return decimal_value


def _parse_timestamp(value: object) -> datetime.datetime:
    """Parse a Chronicle Unix-second or Unix-millisecond timestamp.

    :param value:
        Raw JSON timestamp.
    :return:
        Naive UTC timestamp.
    :raise ChronicleAPIError:
        If the value cannot be interpreted as a positive Unix timestamp.
    """

    try:
        timestamp = int(value)
    except (TypeError, ValueError) as error:
        message = "Chronicle record has an invalid timestamp"
        raise ChronicleAPIError(message) from error
    if timestamp <= 0:
        message = "Chronicle record has a non-positive timestamp"
        raise ChronicleAPIError(message)
    if timestamp > UNIX_MILLISECONDS_THRESHOLD:
        timestamp //= 1000
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)


def _parse_chronicle_record(raw: object) -> ChroniclePricePoint:
    """Normalise one Chronicle history record.

    :param raw:
        Raw JSON record from an operator-configured Chronicle export.
    :return:
        Verified NAV and optional TVL observation.
    :raise ChronicleAPIError:
        If the record does not match the supported public-dashboard shape.
    """

    if not isinstance(raw, dict):
        message = "Chronicle history row must be an object"
        raise ChronicleAPIError(message)
    timestamp = _parse_timestamp(raw.get("timestamp"))
    share_price = _as_decimal(raw.get("nav", raw.get("sharePrice")), "NAV")
    raw_total_assets = raw.get("totalValueLocked", raw.get("totalAssets"))
    total_assets = _as_decimal(raw_total_assets, "total value") if raw_total_assets is not None else None
    return ChroniclePricePoint(timestamp=timestamp, share_price=share_price, total_assets=total_assets)


def fetch_chronicle_price_history(
    feed: ChronicleSecuritizeFeed,
    history_url: str,
    *,
    timeout: float = DEFAULT_CHRONICLE_API_TIMEOUT,
) -> Iterator[ChroniclePricePoint]:
    """Fetch Chronicle Proof of Asset NAV history from an explicit JSON URL.

    The caller must supply the source URL because Chronicle has not documented
    a stable public API endpoint for the STAC dashboard. The response may be a
    list directly or an envelope containing a ``data`` list.

    :param feed:
        Reviewed Chronicle product metadata. Retained for audit-friendly call
        sites and future feed-specific validation.
    :param history_url:
        Public JSON history URL exported by Chronicle.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Oldest-to-newest distinct signed observations.
    :raise ChronicleAPIError:
        If the source response does not contain a supported history list.
    """

    del feed
    response = requests.get(history_url, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        message = "Chronicle history source returned invalid JSON"
        raise ChronicleAPIError(message) from error
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        message = "Chronicle history source must return a list or a data list"
        raise ChronicleAPIError(message)
    price_points = sorted((_parse_chronicle_record(row) for row in rows), key=lambda point: point.timestamp)
    seen_timestamps: set[datetime.datetime] = set()
    for price_point in price_points:
        if price_point.timestamp not in seen_timestamps:
            seen_timestamps.add(price_point.timestamp)
            yield price_point
