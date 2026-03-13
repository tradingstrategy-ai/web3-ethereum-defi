"""Webshare proxy API client and proxy rotation.

Provides helpers to fetch proxy lists from the Webshare API and rotate
through them when rate-limited. Proxy support is optional — when the
``WEBSHARE_API_KEY`` environment variable is not set, all functions
gracefully return ``None``.

Supports both datacenter proxies (mode=direct) and residential proxies
(mode=backbone or mode=direct). The mode can be configured via
``WEBSHARE_PROXY_MODE`` environment variable, defaulting to "backbone"
(residential/server proxies).

:see: https://apidocs.webshare.io/proxy-list/list
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import requests

from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)

#: Webshare rotating gateway for backbone/residential proxies
BACKBONE_PROXY_HOST = "p.webshare.io"

#: Webshare proxy list endpoint template (mode will be inserted)
PROXY_LIST_URL_TEMPLATE = "https://proxy.webshare.io/api/v2/proxy/list/?mode={mode}&page=1&page_size=100"

#: HTTP timeout for Webshare API calls
API_TIMEOUT_SECONDS = 30

#: Default location for proxy state file
DEFAULT_PROXY_STATE_PATH = Path("~/.tradingstrategy/webshare-proxy-state.json").expanduser()

#: Grace period in days before retrying failed proxies
GRACE_PERIOD_DAYS = 14


@dataclass(slots=True)
class FailedProxyEntry:
    """Tracks a failed proxy with timestamp and reason."""

    #: When the proxy failure was recorded (naive UTC)
    failed_at: datetime.datetime
    #: Reason for failure (e.g., 'ssl_protocol_error', 'rate_limited')
    reason: str
    #: Number of times this proxy has failed
    failure_count: int = 1


@dataclass(slots=True)
class ProxyStateManager:
    """Manages persistent state for failed proxies.

    Stores failure information in a JSON file and enforces a grace period
    before retrying failed proxies.
    """

    #: Path to the JSON state file
    state_path: Path = field(default_factory=lambda: DEFAULT_PROXY_STATE_PATH)
    #: Log level for proxy failure messages (e.g. ``logging.DEBUG`` to suppress)
    log_level: int = logging.WARNING
    #: Failed proxies indexed by proxy identifier
    _failed_proxies: dict[str, FailedProxyEntry] = field(default_factory=dict, init=False)

    @staticmethod
    def get_proxy_id(proxy: WebshareProxy) -> str:
        """Generate a consistent identifier for a proxy.

        For direct proxies, uses address:port format.
        For backbone/residential proxies (no address), uses username.
        """
        if proxy.proxy_address is not None:
            return f"{proxy.proxy_address}:{proxy.port}"
        return proxy.username

    def load(self) -> None:
        """Load failed proxy state from JSON file."""
        if not self.state_path.exists():
            logger.debug("No proxy state file found at %s", self.state_path)
            return

        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)

            version = data.get("version", 1)
            if version != 1:
                logger.warning("Unknown proxy state file version %d, ignoring", version)
                return

            for proxy_id, entry_data in data.get("failed_proxies", {}).items():
                self._failed_proxies[proxy_id] = FailedProxyEntry(
                    failed_at=datetime.datetime.fromisoformat(entry_data["failed_at"]),
                    reason=entry_data["reason"],
                    failure_count=entry_data.get("failure_count", 1),
                )

            logger.info(
                "Loaded %d failed proxy entries from %s",
                len(self._failed_proxies),
                self.state_path,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to load proxy state file: %s", e)

    def save(self) -> None:
        """Save failed proxy state to JSON file."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "failed_proxies": {
                proxy_id: {
                    "failed_at": entry.failed_at.isoformat(),
                    "reason": entry.reason,
                    "failure_count": entry.failure_count,
                }
                for proxy_id, entry in self._failed_proxies.items()
            },
        }

        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.debug(
            "Saved %d failed proxy entries to %s",
            len(self._failed_proxies),
            self.state_path,
        )

    def is_blocked(self, proxy: WebshareProxy) -> bool:
        """Check if a proxy is blocked due to recent failure.

        :returns: True if proxy failed within the grace period
        """
        proxy_id = self.get_proxy_id(proxy)
        entry = self._failed_proxies.get(proxy_id)
        if entry is None:
            return False

        grace_cutoff = native_datetime_utc_now() - datetime.timedelta(days=GRACE_PERIOD_DAYS)
        return entry.failed_at > grace_cutoff

    def record_failure(self, proxy: WebshareProxy, reason: str) -> None:
        """Record a proxy failure.

        Updates the failure timestamp and increments the failure count.
        """
        proxy_id = self.get_proxy_id(proxy)
        existing = self._failed_proxies.get(proxy_id)
        now = native_datetime_utc_now()

        if existing is not None:
            self._failed_proxies[proxy_id] = FailedProxyEntry(
                failed_at=now,
                reason=reason,
                failure_count=existing.failure_count + 1,
            )
        else:
            self._failed_proxies[proxy_id] = FailedProxyEntry(
                failed_at=now,
                reason=reason,
                failure_count=1,
            )

        logger.log(
            self.log_level,
            "Recorded failure for proxy %s: %s (count: %d)",
            proxy_id,
            reason,
            self._failed_proxies[proxy_id].failure_count,
        )
        self.save()

    def cleanup_expired(self) -> int:
        """Remove entries older than the grace period.

        :returns: Number of entries removed
        """
        grace_cutoff = native_datetime_utc_now() - datetime.timedelta(days=GRACE_PERIOD_DAYS)

        expired = [proxy_id for proxy_id, entry in self._failed_proxies.items() if entry.failed_at <= grace_cutoff]

        for proxy_id in expired:
            del self._failed_proxies[proxy_id]

        if expired:
            logger.info("Cleaned up %d expired proxy failure entries", len(expired))
            self.save()

        return len(expired)

    def get_blocked_count(self) -> int:
        """Return the number of currently blocked proxies."""
        grace_cutoff = native_datetime_utc_now() - datetime.timedelta(days=GRACE_PERIOD_DAYS)
        return sum(1 for e in self._failed_proxies.values() if e.failed_at > grace_cutoff)


@dataclass(frozen=True, slots=True)
class WebshareProxy:
    """A single proxy entry from the Webshare API.

    For backbone/residential proxies, ``proxy_address`` will be None and
    connections go through the rotating gateway at ``p.webshare.io``.
    """

    #: Proxy server hostname or IP address (None for backbone proxies)
    proxy_address: str | None
    #: Proxy server port
    port: int
    #: Authentication username
    username: str
    #: Authentication password
    password: str
    #: Two-letter country code (e.g. "FI", "US")
    country_code: str
    #: City name reported by Webshare
    city_name: str | None

    def to_playwright_proxy(self) -> dict[str, str]:
        """Convert to a Playwright-compatible proxy configuration dict.

        For backbone/residential proxies (where proxy_address is None),
        uses the Webshare rotating gateway.
        """
        if self.proxy_address is None:
            server = f"http://{BACKBONE_PROXY_HOST}:{self.port}"
        else:
            server = f"http://{self.proxy_address}:{self.port}"

        return {
            "server": server,
            "username": self.username,
            "password": self.password,
        }

    def to_proxy_url(self) -> str:
        """Convert to a proxy URL string for ``requests``.

        Format: ``http://user:pass@host:port``

        For backbone/residential proxies (where proxy_address is None),
        uses the Webshare rotating gateway.
        """
        host = self.proxy_address if self.proxy_address else BACKBONE_PROXY_HOST
        user = quote(self.username, safe="")
        pwd = quote(self.password, safe="")
        return f"http://{user}:{pwd}@{host}:{self.port}"


@dataclass(slots=True)
class ProxyRotator:
    """Manages a pool of proxies and rotates through them on demand.

    Thread-safe rotation is ensured by a :class:`threading.Lock` and a
    generation counter so that concurrent 429 responses only trigger a
    single rotation.

    When a :class:`ProxyStateManager` is provided, failed proxies are tracked
    and skipped during rotation based on a grace period.
    """

    #: All available proxies
    proxies: list[WebshareProxy]
    #: Optional state manager for tracking failed proxies
    state_manager: ProxyStateManager | None = None
    #: Total proxies returned from API (before filtering blocked)
    total_from_api: int = 0
    #: Number of proxies blocked due to recent failures
    blocked_count: int = 0
    #: Log level for proxy rotation messages (e.g. ``logging.DEBUG`` to suppress)
    log_level: int = logging.WARNING
    #: Index of the currently active proxy
    _current_index: int = field(default=0, init=False, repr=False)
    #: Monotonically increasing generation; bumped on each rotation
    _generation: int = field(default=0, init=False, repr=False)
    #: Guards concurrent rotation attempts
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def current(self) -> WebshareProxy:
        """Return the currently active proxy."""
        return self.proxies[self._current_index]

    @property
    def generation(self) -> int:
        """Current rotation generation (useful to deduplicate concurrent rotations)."""
        return self._generation

    def record_failure(self, reason: str) -> None:
        """Record a failure for the current proxy.

        Only records if a state manager is configured.
        """
        if self.state_manager is not None:
            self.state_manager.record_failure(self.current(), reason)

    def rotate(self, expected_generation: int | None = None, failure_reason: str | None = None) -> WebshareProxy:
        """Advance to the next proxy in the pool.

        If *expected_generation* is given and differs from the current
        generation, someone else already rotated — return the current proxy
        without rotating again.

        If *failure_reason* is provided, records the current proxy as failed
        before rotating.
        """
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                logger.debug(
                    "Rotation already happened (gen %d -> %d), skipping",
                    expected_generation,
                    self._generation,
                )
                return self.current()

            if failure_reason is not None:
                self.record_failure(failure_reason)

            self._current_index = (self._current_index + 1) % len(self.proxies)
            self._generation += 1
            proxy = self.current()
            logger.log(
                self.log_level,
                "Rotated to proxy %s:%d (%s/%s) [gen %d]",
                proxy.proxy_address,
                proxy.port,
                proxy.country_code,
                proxy.city_name,
                self._generation,
            )
            return proxy

    def clone_for_worker(self, start_index: int = 0) -> ProxyRotator:
        """Create a worker-specific clone with its own rotation state.

        The clone shares the same proxy list and
        :class:`ProxyStateManager` (so failures are recorded globally)
        but gets its own ``_current_index`` and ``_lock``. This allows
        each worker thread to start on a different proxy and rotate
        independently.

        :param start_index:
            Starting proxy index for this worker. Typically the worker
            ordinal (0, 1, 2, ...) so proxies are distributed evenly.
        :return:
            A new :class:`ProxyRotator` sharing the state manager.
        """
        clone = ProxyRotator(
            proxies=self.proxies,
            state_manager=self.state_manager,
            total_from_api=self.total_from_api,
            blocked_count=self.blocked_count,
            log_level=self.log_level,
        )
        clone._current_index = start_index % len(self.proxies) if self.proxies else 0
        return clone

    def __len__(self) -> int:
        return len(self.proxies)


def _parse_proxy_results(results: list[dict]) -> list[WebshareProxy]:
    """Parse API results into WebshareProxy objects.

    :param results:
        Raw proxy entries from the Webshare API.
    :return:
        List of valid proxy objects.
    """
    proxies = [
        WebshareProxy(
            proxy_address=entry.get("proxy_address"),
            port=int(entry["port"]),
            username=entry["username"],
            password=entry["password"],
            country_code=entry.get("country_code", ""),
            city_name=entry.get("city_name"),
        )
        for entry in results
        if entry.get("valid", False)
    ]
    logger.info("Filtered to %d valid proxies", len(proxies))
    return proxies


def fetch_proxy_list(api_key: str, mode: str = "backbone") -> list[WebshareProxy]:
    """Fetch the proxy list from the Webshare API.

    :param api_key: Webshare API token
    :param mode: Proxy mode - "direct" for datacenter or "backbone" for residential
    :returns: list of valid proxies
    :raises requests.HTTPError: on non-2xx responses
    """
    url = PROXY_LIST_URL_TEMPLATE.format(mode=mode)
    resp = requests.get(
        url,
        headers={"Authorization": f"Token {api_key}"},
        timeout=API_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()

    data = resp.json()
    results = data.get("results", [])
    logger.info("Webshare API returned %d proxies (mode=%s)", len(results), mode)

    proxies = _parse_proxy_results(results)
    if not proxies:
        logger.warning("No valid proxies returned from Webshare")
    return proxies


#: URL used for proxy health checks (returns JSON with the origin IP)
_HEALTH_CHECK_URL = "http://httpbin.org/ip"
#: Maximum proxy rotations during health check before giving up
_HEALTH_CHECK_MAX_ROTATIONS = 3


def check_proxy_health(rotator: ProxyRotator, log_level: int = logging.WARNING) -> bool:
    """Verify that the current proxy can reach the internet.

    Makes a GET request to :data:`_HEALTH_CHECK_URL` via the proxy and
    logs the resulting external IP. On failure, rotates and retries up
    to :data:`_HEALTH_CHECK_MAX_ROTATIONS` times.

    :returns: ``True`` if a working proxy was found, ``False`` otherwise
    """
    for attempt in range(1, _HEALTH_CHECK_MAX_ROTATIONS + 1):
        proxy_url = rotator.current().to_proxy_url()
        proxy_dict = {"http": proxy_url, "https": proxy_url}
        try:
            resp = requests.get(
                _HEALTH_CHECK_URL,
                proxies=proxy_dict,
                timeout=15,
            )
            resp.raise_for_status()
            origin_ip = resp.json().get("origin", "unknown")
            logger.log(
                log_level,
                "Proxy health check OK — external IP: %s (attempt %d/%d)",
                origin_ip,
                attempt,
                _HEALTH_CHECK_MAX_ROTATIONS,
            )
            return True
        except Exception as exc:
            logger.log(
                log_level,
                "Proxy health check FAILED (attempt %d/%d): %s",
                attempt,
                _HEALTH_CHECK_MAX_ROTATIONS,
                exc,
            )
            if attempt < _HEALTH_CHECK_MAX_ROTATIONS:
                rotator.rotate(failure_reason="health_check_failed")

    logger.error(
        "Proxy health check failed after %d attempts — proxies may be down",
        _HEALTH_CHECK_MAX_ROTATIONS,
    )
    return False


def load_proxy_rotator() -> ProxyRotator | None:
    """Load a :class:`ProxyRotator` from the ``WEBSHARE_API_KEY`` env var.

    Returns ``None`` when the environment variable is not set, meaning
    proxy support is disabled and the scraper will connect directly.

    The proxy mode can be configured via ``WEBSHARE_PROXY_MODE`` environment
    variable (defaults to "backbone" for residential/server proxies). Set to
    "direct" for datacenter proxies (requires datacenter plan).

    Proxies that have failed within the grace period (14 days) are filtered
    out. The remaining proxies are randomised to distribute load.
    """
    api_key = os.environ.get("WEBSHARE_API_KEY", "").strip()
    if not api_key:
        logger.debug("WEBSHARE_API_KEY not set — proxies disabled")
        return None

    mode = os.environ.get("WEBSHARE_PROXY_MODE", "backbone").strip().lower()
    if mode not in {"direct", "backbone"}:
        logger.warning("Invalid WEBSHARE_PROXY_MODE=%s, defaulting to direct", mode)
        mode = "backbone"

    proxies = fetch_proxy_list(api_key, mode=mode)
    if not proxies:
        logger.warning("Webshare returned no valid proxies — falling back to direct")
        return None

    state_manager = ProxyStateManager()
    state_manager.load()
    state_manager.cleanup_expired()

    total_count = len(proxies)
    proxies = [p for p in proxies if not state_manager.is_blocked(p)]
    blocked_count = total_count - len(proxies)

    if blocked_count > 0:
        logger.info(
            "Filtered out %d blocked proxies (%d remaining)",
            blocked_count,
            len(proxies),
        )

    if not proxies:
        logger.warning(
            "All %d proxies are blocked — falling back to direct connection",
            total_count,
        )
        return None

    random.shuffle(proxies)
    logger.info("Randomised order of %d proxies", len(proxies))

    return ProxyRotator(
        proxies=proxies,
        state_manager=state_manager,
        total_from_api=total_count,
        blocked_count=blocked_count,
    )


def load_proxy_urls(api_key: str | None = None) -> list[str]:
    """Load proxy URLs from the Webshare API.

    Reads ``WEBSHARE_API_KEY`` and ``WEBSHARE_PROXY_MODE`` from environment
    variables (or accepts an explicit *api_key*).

    Filters out blocked proxies and shuffles the result for load
    distribution. Each URL is in ``http://user:pass@host:port`` format,
    suitable for passing as ``proxies`` to ``requests.Session.post()``.

    :param api_key:
        Webshare API key. If None, reads from ``WEBSHARE_API_KEY`` env var.
    :return:
        List of proxy URL strings. Empty list if no API key or no
        proxies available.
    """
    if api_key is None:
        api_key = os.environ.get("WEBSHARE_API_KEY", "").strip()
    if not api_key:
        logger.debug("WEBSHARE_API_KEY not set — proxies disabled")
        return []

    mode = os.environ.get("WEBSHARE_PROXY_MODE", "backbone").strip().lower()
    if mode not in {"direct", "backbone"}:
        logger.warning("Invalid WEBSHARE_PROXY_MODE=%s, defaulting to direct", mode)
        mode = "backbone"

    proxies = fetch_proxy_list(api_key, mode=mode)
    if not proxies:
        return []

    state_manager = ProxyStateManager()
    state_manager.load()
    state_manager.cleanup_expired()

    total_count = len(proxies)
    proxies = [p for p in proxies if not state_manager.is_blocked(p)]
    blocked_count = total_count - len(proxies)

    if blocked_count > 0:
        logger.info(
            "Filtered out %d blocked proxies (%d remaining)",
            blocked_count,
            len(proxies),
        )

    if not proxies:
        logger.warning("All %d proxies are blocked — returning empty list", total_count)
        return []

    random.shuffle(proxies)
    logger.info("Loaded %d proxy URLs (mode=%s)", len(proxies), mode)

    return [p.to_proxy_url() for p in proxies]


def print_proxy_dashboard(rotator: ProxyRotator | None) -> None:
    """Print a dashboard showing proxy pool status.

    Displays total proxies from API, active (available) proxies, and
    blocked proxies due to recent failures.
    """
    print()  # noqa: T201
    print("=" * 40)  # noqa: T201
    print("PROXY DASHBOARD")  # noqa: T201
    print("=" * 40)  # noqa: T201

    if rotator is None:
        print("Status: DISABLED (no API key or no proxies)")  # noqa: T201
        print("=" * 40)  # noqa: T201
        print()  # noqa: T201
        return

    total = rotator.total_from_api
    active = len(rotator.proxies)
    blocked = rotator.blocked_count

    print(f"Total from API:     {total:>4}")  # noqa: T201
    print(f"Active (available): {active:>4}")  # noqa: T201
    print(f"Blocked (grace):    {blocked:>4}")  # noqa: T201
    print("-" * 40)  # noqa: T201
    print(f"Grace period:       {GRACE_PERIOD_DAYS} days")  # noqa: T201
    print("=" * 40)  # noqa: T201
    print()  # noqa: T201

    logger.info(
        "Proxy dashboard: total=%d, active=%d, blocked=%d",
        total,
        active,
        blocked,
    )
