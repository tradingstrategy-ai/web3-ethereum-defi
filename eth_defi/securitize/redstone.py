"""Read Securitize fund NAV history from RedStone's public price API.

RedStone publishes the Securitize feeds used here as signed fundamental-value
feeds. The API contains repeated publications of the same daily NAV, so this
module queries one observation at each requested daily checkpoint rather than
downloading every relay publication.

The public endpoint is undocumented. Its response is therefore validated
strictly and only recognised, reviewed Securitize token addresses are exposed.

Reference: https://app.redstone.finance/app/feeds/
"""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from eth_typing import HexAddress

from eth_defi.securitize.description import ACRED_ETHEREUM, HLSCOPE_ETHEREUM, STAC_ETHEREUM, VBILL_ETHEREUM

#: Public RedStone price endpoint used by its feed dashboard.
REDSTONE_PRICE_API_URL = "https://api.redstone.finance/prices"

#: Timeout for a public RedStone API request in seconds.
DEFAULT_REDSTONE_API_TIMEOUT = 20.0


class RedstoneAPIError(RuntimeError):
    """Raised when the RedStone public feed API returns malformed data."""


@dataclass(slots=True, frozen=True)
class RedstoneSecuritizeFeed:
    """One reviewed Securitize NAV feed available through RedStone."""

    #: EVM chain hosting the Securitize DSToken.
    chain_id: int

    #: Lower-case DSToken address.
    token: HexAddress

    #: RedStone fundamental feed identifier.
    feed_id: str


@dataclass(slots=True, frozen=True)
class RedstonePricePoint:
    """One signed RedStone fundamental NAV observation."""

    #: UTC timestamp at which RedStone published the value.
    timestamp: datetime.datetime

    #: Fund NAV per share in USD.
    share_price: Decimal


#: Reviewed Securitize products with RedStone fundamental NAV feeds.
REDSTONE_SECURITIZE_FEEDS: dict[tuple[int, HexAddress], RedstoneSecuritizeFeed] = {
    (ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token): RedstoneSecuritizeFeed(ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token, "ACRED_FUNDAMENTAL"),
    (HLSCOPE_ETHEREUM.chain_id, HLSCOPE_ETHEREUM.token): RedstoneSecuritizeFeed(HLSCOPE_ETHEREUM.chain_id, HLSCOPE_ETHEREUM.token, "HLScope_FUNDAMENTAL"),
    (STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token): RedstoneSecuritizeFeed(STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token, "STAC_FUNDAMENTAL"),
    (VBILL_ETHEREUM.chain_id, VBILL_ETHEREUM.token): RedstoneSecuritizeFeed(VBILL_ETHEREUM.chain_id, VBILL_ETHEREUM.token, "VBILL_ETHEREUM_FUNDAMENTAL"),
}


def _as_naive_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalise a datetime to a naive UTC value.

    :param value:
        A naive UTC or timezone-aware datetime.
    :return:
        Naive UTC datetime.
    """

    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _parse_price_point(raw: object, feed_id: str) -> RedstonePricePoint:
    """Validate and normalise one RedStone API response row.

    :param raw:
        Raw JSON row returned by RedStone.
    :param feed_id:
        Requested feed identifier, used in diagnostic errors.
    :return:
        Parsed NAV observation.
    :raise RedstoneAPIError:
        If a required field is missing or malformed.
    """

    if not isinstance(raw, dict):
        raise RedstoneAPIError(f"RedStone {feed_id} response row must be an object")
    try:
        timestamp_ms = int(raw["timestamp"])
        share_price = Decimal(str(raw["value"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise RedstoneAPIError(f"RedStone {feed_id} response row is missing a valid timestamp or value") from error
    if timestamp_ms <= 0 or share_price <= 0:
        raise RedstoneAPIError(f"RedStone {feed_id} response row has a non-positive timestamp or value")
    return RedstonePricePoint(
        timestamp=datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.UTC).replace(tzinfo=None),
        share_price=share_price,
    )


def fetch_redstone_price_at(
    feed: RedstoneSecuritizeFeed,
    at: datetime.datetime,
    *,
    api_url: str = REDSTONE_PRICE_API_URL,
    timeout: float = DEFAULT_REDSTONE_API_TIMEOUT,
) -> RedstonePricePoint | None:
    """Fetch the latest RedStone NAV observation at or before a timestamp.

    The API's ``toTimestamp`` argument is milliseconds since Unix epoch. A
    one-row request is intentional: Securitize fundamental feeds publish many
    relays of the same daily NAV and the historical scanner only needs the
    latest value available at each valuation checkpoint.

    :param feed:
        Reviewed Securitize RedStone feed.
    :param at:
        UTC valuation checkpoint.
    :param api_url:
        Override for the public endpoint, primarily for integration tests.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Latest observation at or before ``at``, or ``None`` when the feed had
        not yet published a value.
    :raise RedstoneAPIError:
        If the provider returns a malformed response.
    """

    at = _as_naive_utc(at)
    response = requests.get(
        api_url,
        params={
            "symbol": feed.feed_id,
            "provider": "redstone",
            "limit": 1,
            "toTimestamp": int(at.replace(tzinfo=datetime.UTC).timestamp() * 1000),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise RedstoneAPIError(f"RedStone {feed.feed_id} returned invalid JSON") from error
    if not isinstance(payload, list):
        raise RedstoneAPIError(f"RedStone {feed.feed_id} response must be a list")
    if not payload:
        return None
    price_point = _parse_price_point(payload[0], feed.feed_id)
    if price_point.timestamp > at:
        raise RedstoneAPIError(f"RedStone {feed.feed_id} returned an observation after its requested timestamp")
    return price_point


def fetch_redstone_price_history(
    feed: RedstoneSecuritizeFeed,
    start_at: datetime.datetime,
    end_at: datetime.datetime,
    *,
    api_url: str = REDSTONE_PRICE_API_URL,
    timeout: float = DEFAULT_REDSTONE_API_TIMEOUT,
) -> Iterator[RedstonePricePoint]:
    """Fetch a daily checkpointed history for one Securitize RedStone feed.

    This is used for an initial backfill. It includes an anchor observation at
    ``start_at`` and then asks the provider for the value at each following UTC
    midnight. Identical provider publications are emitted once, preserving the
    actual signed timestamp so callers cannot use a NAV before publication.

    :param feed:
        Reviewed Securitize RedStone feed.
    :param start_at:
        Inclusive UTC history boundary.
    :param end_at:
        Inclusive UTC history boundary.
    :param api_url:
        Override for the public endpoint, primarily for integration tests.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Oldest-to-newest distinct RedStone observations.
    :raise ValueError:
        If the requested time range is invalid.
    """

    start_at = _as_naive_utc(start_at)
    end_at = _as_naive_utc(end_at)
    if end_at < start_at:
        message = "end_at must not be earlier than start_at"
        raise ValueError(message)

    seen_timestamps: set[datetime.datetime] = set()
    checkpoint = start_at
    while True:
        price_point = fetch_redstone_price_at(feed, checkpoint, api_url=api_url, timeout=timeout)
        if price_point is not None and price_point.timestamp not in seen_timestamps:
            seen_timestamps.add(price_point.timestamp)
            yield price_point
        if checkpoint == end_at:
            break
        next_midnight = datetime.datetime.combine(checkpoint.date() + datetime.timedelta(days=1), datetime.time.min)
        checkpoint = min(next_midnight, end_at)
