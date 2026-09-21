"""Failover drivers share what they learn about dead hosts.

Scenario behind these tests: one GMX API host is dead at connection level (TLS
handshake dropped) while its mirror is healthy. A bot fetching candles for 100
markets used to send 100 x 3 requests to the dead host, with backoff between
attempts, before the mirror was asked even once.

Only the network boundary (``requests.get`` and the aiohttp session) and the
sleeps are replaced. The real drivers run, so these tests need no credentials.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace

import aiohttp
import pytest
import requests

from eth_defi.gmx import retry as retry_module
from eth_defi.gmx import tier_health
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    GMX_API_URLS_FALLBACK,
    GMX_API_URLS_FALLBACK_2,
)
from eth_defi.gmx.core import oracle as oracle_module
from eth_defi.gmx.core.oracle import OraclePrices, clear_oracle_prices_cache
from eth_defi.gmx.retry import GMXAPIUnavailable, GMXRetryConfig, make_gmx_api_request
from eth_defi.gmx.tier_health import TIER_DOWN_COOLDOWN_SECONDS, clear_tier_health

PRIMARY = GMX_API_URLS["arbitrum"]
BACKUP = GMX_API_URLS_BACKUP["arbitrum"]
FALLBACK = GMX_API_URLS_FALLBACK["arbitrum"]
FALLBACK_2 = GMX_API_URLS_FALLBACK_2["arbitrum"]

#: A price endpoint, so the driver's fifth tier (gmxapi.ai, 404 on prices) is not part of the chain.
CANDLES = "/prices/candles"
PAYLOAD = {"candles": [[1789972738, 100.0, 101.0, 99.0, 100.5]]}

#: 3 attempts per tier, no real waiting, one pass over the chain.
CONFIG = GMXRetryConfig(max_retries=3, initial_delay=0.0, max_delay=0.0, full_cycle_retries=1)


@pytest.fixture(autouse=True)
def _isolated_drivers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate the health record and the oracle cache, and skip real backoff sleeps."""
    clear_tier_health()
    clear_oracle_prices_cache()
    monkeypatch.setattr(retry_module, "time", SimpleNamespace(sleep=lambda _seconds: None))
    monkeypatch.setattr(oracle_module, "time", SimpleNamespace(sleep=lambda _seconds: None, time=lambda: 0.0))
    yield
    clear_tier_health()
    clear_oracle_prices_cache()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """A hand-driven monotonic clock for the health record only."""
    state = {"now": 1000.0}
    monkeypatch.setattr(tier_health, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    return state


def _response(status_code: int, payload: dict) -> requests.Response:
    """A real ``requests.Response`` carrying a JSON body."""
    response = requests.Response()
    response.status_code = status_code
    response.reason = "OK" if status_code < 400 else "Error"  # noqa: PLR2004  # HTTP error threshold literal
    response.url = "https://example.invalid"
    response._content = json.dumps(payload).encode()
    return response


def _tls_dropped() -> requests.exceptions.SSLError:
    """What the dead ``gmxinfra.io`` hosts produce: the TLS handshake is cut."""
    return requests.exceptions.SSLError("SSLEOFError: UNEXPECTED_EOF_WHILE_READING")


def _install_fake_get(monkeypatch: pytest.MonkeyPatch, answers: dict[str, object]) -> list[str]:
    """Route ``requests.get`` by host. A host without an answer has its TLS handshake dropped.

    :param answers:
        Host base URL to a ``requests.Response`` (returned) or an exception (raised).
    :return:
        List every requested URL is appended to, in call order.
    """
    calls: list[str] = []

    def fake_get(url: str, *_args, **_kwargs) -> requests.Response:
        calls.append(url)
        for base, outcome in answers.items():
            if url.startswith(base + "/"):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise _tls_dropped()

    monkeypatch.setattr(retry_module.requests, "get", fake_get)
    return calls


# --- sync driver ---------------------------------------------------------------------------------


def test_sync_dead_host_gets_one_attempt_then_the_mirror_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches: retrying a host whose TLS handshake is cut, three times, before trying the mirror."""
    calls = _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})

    result = make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)

    assert result == PAYLOAD
    assert calls == [PRIMARY + CANDLES, BACKUP + CANDLES]


def test_sync_hundred_requests_do_not_each_rediscover_the_dead_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot's hourly candle refresh: 100 markets against one dead host.

    Catches: failover state that lives only inside a single request, which sent
    100 x 3 = 300 requests to the dead host.
    """
    calls = _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})

    for symbol in range(100):
        make_gmx_api_request("arbitrum", CANDLES, params={"tokenSymbol": f"TOKEN{symbol}"}, retry_config=CONFIG)

    assert calls.count(PRIMARY + CANDLES) == 1
    assert calls.count(BACKUP + CANDLES) == 100  # noqa: PLR2004  # one per market


def test_sync_total_outage_still_walks_every_tier_and_retries_the_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing left to fail over to, the last tier keeps its normal retries.

    Catches: giving up after one attempt everywhere, which would turn a short
    blip on a single remaining host into an immediate failure.
    """
    calls = _install_fake_get(monkeypatch, {})

    with pytest.raises(GMXAPIUnavailable):
        make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)

    assert calls == [PRIMARY + CANDLES, BACKUP + CANDLES, FALLBACK + CANDLES, FALLBACK_2 + CANDLES, FALLBACK_2 + CANDLES, FALLBACK_2 + CANDLES]


def test_sync_server_errors_keep_their_retries_and_do_not_mark_the_host_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5xx is the request's problem, not proof that the host is unreachable.

    Catches: marking a host down on any failure, so that one server error for
    one token would divert every request for five minutes.
    """
    calls = _install_fake_get(monkeypatch, {PRIMARY: _response(500, {}), BACKUP: _response(200, PAYLOAD)})

    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)

    primary, backup = PRIMARY + CANDLES, BACKUP + CANDLES
    assert calls == [primary, primary, primary, backup, primary, primary, primary, backup]


def test_sync_client_error_for_one_request_does_not_poison_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 400 for one unknown token symbol says nothing about the host.

    Catches: marking a host down on a 4xx, which would push all 100 other
    markets off a perfectly healthy host.
    """
    calls = _install_fake_get(monkeypatch, {PRIMARY: _response(400, {}), BACKUP: _response(200, PAYLOAD)})

    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)

    primary, backup = PRIMARY + CANDLES, BACKUP + CANDLES
    assert calls == [primary, backup, primary, backup]


def test_sync_recovered_host_is_asked_first_again_after_the_cooldown(monkeypatch: pytest.MonkeyPatch, clock: dict[str, float]) -> None:
    """Catches: a host that never gets a second chance, so a mirror would carry the traffic forever."""
    answers: dict[str, object] = {BACKUP: _response(200, PAYLOAD)}
    calls = _install_fake_get(monkeypatch, answers)
    primary, backup = PRIMARY + CANDLES, BACKUP + CANDLES

    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    clock["now"] += TIER_DOWN_COOLDOWN_SECONDS + 1
    answers[PRIMARY] = _response(200, PAYLOAD)
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)

    assert calls == [primary, backup, primary, primary]


# --- async driver --------------------------------------------------------------------------------


class _FakeClientResponse:
    """Mirrors the parts of ``aiohttp.ClientResponse`` the driver reads."""

    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self.reason = "OK"
        self.request_info = SimpleNamespace(real_url="https://example.invalid")
        self.history = ()
        self._payload = payload

    async def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:  # noqa: PLR2004  # HTTP error threshold literal
            raise aiohttp.ClientResponseError(self.request_info, self.history, status=self.status, message=self.reason)


class _Exchange:
    """Async context manager returned by ``session.get``. It yields to the loop like real network I/O."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    async def __aenter__(self) -> _FakeClientResponse:
        await asyncio.sleep(0)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *_exc) -> bool:  # mimics the aiohttp context manager
        return False


class _RoutedSession:
    """Routes ``session.get`` by host. A host without an answer refuses the connection."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def get(self, url: str, params: dict | None = None, timeout: object = None) -> _Exchange:  # noqa: ARG002  # mirrors aiohttp
        self.calls.append(url)
        for base, outcome in self.answers.items():
            if url.startswith(base + "/"):
                return _Exchange(outcome)
        host = url.split("//")[1].split("/")[0]
        return _Exchange(aiohttp.ClientConnectorError(SimpleNamespace(host=host, port=443, ssl=True), OSError(111, "Connection refused")))

    async def close(self) -> None:  # noqa: PLR6301  # mimics aiohttp.ClientSession
        return None


@pytest.mark.asyncio
async def test_async_dead_host_gets_one_attempt_then_the_mirror_serves() -> None:
    """Catches: retrying a refused host three times before trying the mirror."""
    session = _RoutedSession({BACKUP: _FakeClientResponse(200, PAYLOAD)})

    result = await async_make_gmx_api_request("arbitrum", CANDLES, session=session, max_retries=3, retry_delay=0)

    assert result == PAYLOAD
    assert session.calls == [PRIMARY + CANDLES, BACKUP + CANDLES]


@pytest.mark.asyncio
async def test_async_concurrent_requests_do_not_retry_a_host_another_request_found_dead() -> None:
    """20 candle requests are in flight at once when the host turns out to be dead.

    Catches: every in-flight request retrying the dead host on its own (60
    requests instead of 20), because the health mark is only set after the
    retries are used up.
    """
    session = _RoutedSession({BACKUP: _FakeClientResponse(200, PAYLOAD)})

    results = await asyncio.gather(*[async_make_gmx_api_request("arbitrum", CANDLES, params={"tokenSymbol": f"TOKEN{i}"}, session=session, max_retries=3, retry_delay=0) for i in range(20)])

    assert results == [PAYLOAD] * 20
    assert sum(call.startswith(PRIMARY) for call in session.calls) == 20  # noqa: PLR2004  # one attempt per request in flight
    assert sum(call.startswith(BACKUP) for call in session.calls) == 20  # noqa: PLR2004


@pytest.mark.asyncio
async def test_async_later_requests_skip_a_host_an_earlier_request_found_dead() -> None:
    """Catches: no memory between sequential requests."""
    session = _RoutedSession({BACKUP: _FakeClientResponse(200, PAYLOAD)})

    await async_make_gmx_api_request("arbitrum", CANDLES, session=session, max_retries=3, retry_delay=0)
    await async_make_gmx_api_request("arbitrum", CANDLES, session=session, max_retries=3, retry_delay=0)

    assert session.calls == [PRIMARY + CANDLES, BACKUP + CANDLES, BACKUP + CANDLES]


# --- oracle query, and sharing between the drivers ------------------------------------------------


def test_oracle_skips_a_host_the_sync_driver_already_found_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the candle refresh learns also serves the signed-price query.

    Catches: one health record per driver, so the oracle would pay again for a
    discovery the candle requests made a moment earlier.
    """
    calls = _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    already_made = len(calls)

    OraclePrices("arbitrum")._make_query(max_retries=1)

    assert calls[already_made:] == [BACKUP + "/signed_prices/latest"]


def test_oracle_still_logs_the_failover_once_the_mirror_has_become_the_first_choice(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """After the primary is marked down, the mirror is tried first. It is still a failover.

    Catches: judging "failover" by position in the reordered list, which would go
    silent exactly while the bot is running on the mirror.
    """
    _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})
    make_gmx_api_request("arbitrum", CANDLES, retry_config=CONFIG)
    oracle = OraclePrices("arbitrum")

    with caplog.at_level(logging.INFO):
        oracle._make_query(max_retries=1)

    assert any(BACKUP in record.getMessage() for record in caplog.records if record.levelno == logging.INFO)


def test_oracle_gives_up_on_a_dead_host_after_one_attempt_when_it_has_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches: five retries with growing backoff on a host whose TLS handshake is cut, while a healthy mirror waits."""
    calls = _install_fake_get(monkeypatch, {BACKUP: _response(200, PAYLOAD)})

    OraclePrices("arbitrum")._make_query(max_retries=5, initial_backoff=0)

    assert calls == [PRIMARY + "/signed_prices/latest", BACKUP + "/signed_prices/latest"]


def test_oracle_total_outage_still_retries_the_last_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches: no retries at all when nothing is left to fail over to."""
    calls = _install_fake_get(monkeypatch, {})
    path = "/signed_prices/latest"

    with pytest.raises(requests.exceptions.RequestException):
        OraclePrices("arbitrum")._make_query(max_retries=2, initial_backoff=0)

    assert calls == [PRIMARY + path, BACKUP + path, FALLBACK + path, FALLBACK_2 + path, FALLBACK_2 + path]
