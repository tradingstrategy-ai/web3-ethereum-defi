"""Validation for GMX ticker payloads.

Shared by the sync and async failover drivers so a ``200 OK`` carrying a
degraded payload (empty list, truncated list, or wrong schema) is treated as
an endpoint failure rather than being returned to callers and cached.
"""

import math
from typing import Any

#: Minimum ticker count a healthy payload must contain, by chain. Testnets
#: serve far fewer tokens than mainnet (Arbitrum Sepolia returns 13 and
#: Avalanche Fuji a similar low count on 2026-08-21); a mainnet-sized floor
#: would reject every valid testnet response.
MIN_EXPECTED_TICKERS_BY_CHAIN: dict[str, int] = {
    "arbitrum_sepolia": 10,
    "avalanche_fuji": 10,
}

#: Default minimum when a chain has no explicit entry above.
DEFAULT_MIN_EXPECTED_TICKERS: int = 100

#: Fields every ticker record must carry for either adapter to read a price.
#: The sync adapter keys on ``tokenAddress`` and reads ``minPrice``/``maxPrice``;
#: the async adapter keys on ``tokenSymbol`` and reads ``minPrice``/``maxPrice``.
_REQUIRED_TICKER_FIELDS: tuple[str, ...] = ("tokenAddress", "tokenSymbol", "minPrice", "maxPrice")

#: Fraction of the last-known-good count that a fresh payload must retain.
#: Survives legitimate GMX delistings while still catching a truncated payload.
_TICKER_COUNT_RATIO_GUARD: float = 0.8


class GMXInvalidPayloadError(RuntimeError):
    """Raised when a GMX endpoint returned a payload that fails validation.

    A ``200`` with a degraded body is a failure of that endpoint: it must be
    failed over and never cached. Subclasses :class:`RuntimeError` so it is
    never mistaken for a programmer-error ``ValueError`` by consumer retriers.
    """


def get_min_expected_tickers(chain: str) -> int:
    """Return the minimum ticker count a healthy payload must contain for a chain.

    :param chain:
        Chain name (e.g. ``"arbitrum"``, ``"arbitrum_sepolia"``).
    :return:
        The chain-specific minimum, or :data:`DEFAULT_MIN_EXPECTED_TICKERS`.
    """
    return MIN_EXPECTED_TICKERS_BY_CHAIN.get(chain.lower(), DEFAULT_MIN_EXPECTED_TICKERS)


def _ticker_record_is_well_formed(record: Any) -> bool:
    """Return ``True`` if one ticker record has all required fields and values.

    A well-formed record carries every field in :data:`_REQUIRED_TICKER_FIELDS`
    with non-empty identifiers, and a ``minPrice``/``maxPrice`` that parse to a
    finite positive float. Zero or missing prices must be rejected: the async
    adapter reads ``minPrice`` with a zero default, so a degenerate record would
    otherwise halve the midpoint price (the P1b degraded-200 bug). ``NaN`` and
    ``Infinity`` must also be rejected — ``float("inf") > 0`` is ``True`` and
    would propagate an unusable price into the cached payload.
    """
    if not isinstance(record, dict):
        return False
    if not all(field in record for field in _REQUIRED_TICKER_FIELDS):
        return False
    if not record["tokenAddress"] or not record["tokenSymbol"]:
        return False
    try:
        min_price = float(record["minPrice"])
        max_price = float(record["maxPrice"])
    except (TypeError, ValueError):
        return False
    return math.isfinite(min_price) and math.isfinite(max_price) and min_price > 0 and max_price > 0


def validate_tickers_payload(
    payload: Any,
    min_expected_tickers: int = DEFAULT_MIN_EXPECTED_TICKERS,
    last_good_count: int | None = None,
) -> bool:
    """Return ``True`` if ``payload`` looks like a healthy ticker list.

    A ticker list is healthy when it is a non-empty list with at least
    ``min_expected_tickers`` entries (or ``_TICKER_COUNT_RATIO_GUARD`` of
    ``last_good_count`` when known) and every record is well-formed per
    :func:`_ticker_record_is_well_formed`.

    :param payload:
        The parsed JSON body returned by ``/prices/tickers``.
    :param min_expected_tickers:
        Floor for a cold process with no last-known-good count.
    :param last_good_count:
        Number of tickers in the last successfully validated payload. When
        set, the ratio guard (rather than the floor) is the threshold, so
        legitimate delistings do not fail validation.
    :return:
        ``True`` when the payload passes; ``False`` otherwise.
    """
    if not isinstance(payload, list) or not payload:
        return False

    if last_good_count:
        threshold = int(last_good_count * _TICKER_COUNT_RATIO_GUARD)
    else:
        threshold = min_expected_tickers

    if len(payload) < threshold:
        return False

    return all(_ticker_record_is_well_formed(record) for record in payload)
