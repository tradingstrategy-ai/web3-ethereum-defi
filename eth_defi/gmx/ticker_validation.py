"""Validation for GMX ticker payloads.

Shared by the sync and async failover drivers so a ``200 OK`` carrying a
degraded payload (empty list, truncated list, or wrong schema) is treated as
an endpoint failure rather than being returned to callers and cached.
"""

from typing import Any


class GMXInvalidPayloadError(ValueError):
    """Raised when a GMX endpoint returned a payload that fails validation.

    A ``200`` with a degraded body is a failure of that endpoint: it must be
    failed over and never cached. Subclasses :class:`ValueError` so callers
    that already catch ``ValueError`` for programmer errors keep working, but
    the failover driver checks for this type specifically.
    """


def validate_tickers_payload(
    payload: Any,
    min_expected_tickers: int = 100,
    last_good_count: int | None = None,
) -> bool:
    """Return ``True`` if ``payload`` looks like a healthy ticker list.

    A ticker list is healthy when it is a non-empty list with at least
    ``min_expected_tickers`` entries (or 80 % of ``last_good_count``, whichever
    is larger — the ratio guard survives legitimate GMX delistings while still
    catching a truncated payload) and the first five entries carry the fields
    the ccxt adapter reads (``tokenAddress`` and ``maxPrice``).

    :param payload:
        The parsed JSON body returned by ``/prices/tickers``.
    :param min_expected_tickers:
        Floor for a cold process with no last-known-good count.
    :param last_good_count:
        Number of tickers in the last successfully validated payload, used for
        the 80 % ratio guard. ``None`` disables the ratio guard.
    :return:
        ``True`` when the payload passes; ``False`` otherwise.
    """
    if not isinstance(payload, list) or not payload:
        return False

    threshold = max(min_expected_tickers, int((last_good_count or 0) * 0.8))
    if len(payload) < threshold:
        return False

    sample = payload[:5]
    return all(isinstance(t, dict) and "tokenAddress" in t and "maxPrice" in t for t in sample)
