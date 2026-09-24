"""Shared health record for GMX API hosts.

``make_gmx_api_request``, ``async_make_gmx_api_request`` and
``OraclePrices._make_query`` each fail over from tier to tier *per request*.
Without shared memory every request rediscovers that a dead host is dead: 100
markets at 3 attempts each is 300 requests, and several seconds of backoff
apiece, before the first byte from a healthy tier.

A host that fails at connection level (refused, TLS handshake, DNS, connect
timeout) is recorded here as down for a cooldown. Requests then try healthy
tiers first and give up on a down tier after one attempt. Down tiers are only
moved to the back, never removed, so a total outage still walks every tier.

Health is kept per host, so what one endpoint learns applies to all of them
(``/prices/candles`` finding a host dead also spares ``/signed_prices/latest``).
"""

import threading
import time
from collections.abc import Callable, Sequence
from typing import TypeVar
from urllib.parse import urlsplit

T = TypeVar("T")

#: Seconds a host stays marked down after a connection-level failure.
#: After that the next request tries it again first.
TIER_DOWN_COOLDOWN_SECONDS = 300.0

_lock = threading.Lock()

#: Host -> ``time.monotonic()`` moment until which the host counts as down.
_down_until: dict[str, float] = {}


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def mark_tier_down(url: str, cooldown: float = TIER_DOWN_COOLDOWN_SECONDS) -> bool:
    """Record that the host of ``url`` failed at connection level.

    :param url:
        Any URL on the host, the endpoint does not matter.
    :param cooldown:
        Seconds the host stays marked down.
    :return:
        ``True`` if the host was up until now, ``False`` if it was already marked down.
        Callers log one WARNING on ``True`` and stay quiet otherwise.
    """
    host = _host(url)
    now = time.monotonic()
    with _lock:
        was_down = _down_until.get(host, 0.0) > now
        _down_until[host] = now + cooldown
    return not was_down


def is_tier_down(url: str) -> bool:
    """Check whether the host of ``url`` is inside its cooldown.

    An expired mark is dropped here, so the first request after the cooldown probes the host again.
    """
    host = _host(url)
    with _lock:
        until = _down_until.get(host)
        if until is None:
            return False
        if until <= time.monotonic():
            del _down_until[host]
            return False
        return True


def healthy_first(tiers: Sequence[T], key: Callable[[T], str] = str) -> list[T]:
    """Order tiers so that hosts marked down come last.

    Nothing is dropped and the configured order is kept inside each group, so
    when every tier is down (or every tier is up) the result is the input order.

    :param tiers:
        Tiers in configured failover order.
    :param key:
        Returns the URL of a tier, for tiers that are not plain URL strings.
    :return:
        A new list, healthy tiers first.
    """
    down_flags = [is_tier_down(key(tier)) for tier in tiers]
    healthy = [tier for tier, down in zip(tiers, down_flags) if not down]
    down = [tier for tier, is_down in zip(tiers, down_flags) if is_down]
    return healthy + down


def clear_tier_health() -> None:
    """Forget every mark. Meant for tests."""
    with _lock:
        _down_until.clear()
