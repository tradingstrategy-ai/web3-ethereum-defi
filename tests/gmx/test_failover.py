import time
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
import requests
from ccxt import ExchangeNotAvailable

from eth_defi.gmx.api import _TICKER_PRICES_CACHE, GMXAPI  # noqa: PLC2701  # cache inspection required by the failover tests
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX  # noqa: N811  # async exchange alias used throughout the codebase
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.retry import (
    GMXAPIUnavailable,
    GMXRetryConfig,
    is_retryable_http_status,
    make_gmx_api_request,
)
from eth_defi.gmx.ticker_validation import GMXInvalidPayloadError, validate_tickers_payload


def _ticker(address: str = "0xaaa", max_price: str = "1000") -> dict:
    return {"tokenAddress": address, "maxPrice": max_price}


def test_validate_tickers_payload_accepts_healthy_payload():
    payload = [_ticker(f"0x{i:03x}") for i in range(120)]
    assert validate_tickers_payload(payload) is True


def test_validate_tickers_payload_rejects_non_list():
    assert validate_tickers_payload({"markets": []}) is False


def test_validate_tickers_payload_rejects_empty_list():
    assert validate_tickers_payload([]) is False


def test_validate_tickers_payload_rejects_below_minimum_count():
    payload = [_ticker() for _ in range(10)]
    assert validate_tickers_payload(payload, min_expected_tickers=100) is False


def test_validate_tickers_payload_rejects_bad_schema_in_first_five():
    payload = [{"tokenAddress": "0x1"}] * 5 + [_ticker() for _ in range(120)]
    assert validate_tickers_payload(payload) is False


def test_validate_tickers_payload_rejects_truncated_below_ratio():
    payload = [_ticker() for _ in range(110)]
    # last_good_count=124 -> 80% floor = 99; 110 passes
    assert validate_tickers_payload(payload, last_good_count=124) is True
    truncated = [_ticker() for _ in range(90)]
    assert validate_tickers_payload(truncated, last_good_count=124) is False


def test_is_retryable_http_status_classification():
    assert is_retryable_http_status(500) is True
    assert is_retryable_http_status(503) is True
    assert is_retryable_http_status(408) is True
    assert is_retryable_http_status(429) is True
    assert is_retryable_http_status(404) is False
    assert is_retryable_http_status(400) is False
    assert is_retryable_http_status(403) is False


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.reason = "mock"
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:  # noqa: PLR2004  # HTTP error threshold literal
            raise requests.HTTPError(f"{self.status_code} {self.reason}", response=self)


def test_make_gmx_api_request_fails_over_on_404_without_backoff():
    # Primary 404s immediately; backup returns healthy data.
    # max_retries=3 makes the "no backoff" assertion meaningful: a retryable
    # failure would retry + sleep, but a 404 must fail over instantly.
    primary = _FakeResponse(404, [])
    backup = _FakeResponse(200, {"ok": True})

    with patch("eth_defi.gmx.retry.requests.get", side_effect=[primary, backup]) as get:
        with patch("eth_defi.gmx.retry.time.sleep", new_callable=MagicMock) as sleep_mock:
            result = make_gmx_api_request(
                chain="arbitrum",
                endpoint="/prices/tickers",
                retry_config=GMXRetryConfig(
                    max_retries=3,
                    initial_delay=0.01,
                    max_delay=0.05,
                    backoff_multiplier=2.0,
                    full_cycle_retries=1,
                ),
            )

    assert result == {"ok": True}
    # Primary is hit exactly once (no retry/backoff on 404), backup once.
    assert get.call_count == 2  # noqa: PLR2004
    # 404 fails over immediately — exponential backoff must never run.
    sleep_mock.assert_not_called()


def test_make_gmx_api_request_raises_gmxapiunavailable_on_total_failure():
    failing = _FakeResponse(500, {})
    with patch("eth_defi.gmx.retry.requests.get", return_value=failing):
        with pytest.raises(GMXAPIUnavailable):
            make_gmx_api_request(
                chain="arbitrum",
                endpoint="/prices/tickers",
                retry_config=GMXRetryConfig.create_test_config(),
            )


def test_make_gmx_api_request_validate_rejects_degraded_payload_and_fails_over():
    degraded = _FakeResponse(200, [])
    healthy = _FakeResponse(200, [{"tokenAddress": "0x1", "maxPrice": "100"} for _ in range(120)])

    with patch("eth_defi.gmx.retry.requests.get", side_effect=[degraded, healthy]):
        result = make_gmx_api_request(
            chain="arbitrum",
            endpoint="/prices/tickers",
            retry_config=GMXRetryConfig.create_test_config(),
            validate=lambda p: validate_tickers_payload(p, min_expected_tickers=100),
        )

    assert len(result) == 120  # noqa: PLR2004


def test_gmxapiunavailable_carries_attempt_summary():
    err = GMXAPIUnavailable("arbitrum", "/prices/tickers", ("primary: 500", "backup: 500"))
    assert "primary: 500" in str(err)
    assert isinstance(err, RuntimeError)


def test_make_gmx_api_request_attempts_summary_covers_all_five_tiers():
    failing = _FakeResponse(500, {})
    with patch("eth_defi.gmx.retry.requests.get", return_value=failing):
        with pytest.raises(GMXAPIUnavailable) as exc_info:
            make_gmx_api_request(
                chain="arbitrum",
                endpoint="/prices/tickers",
                retry_config=GMXRetryConfig.create_test_config(),
            )

    err = exc_info.value
    assert len(err.attempts) == 5  # noqa: PLR2004  # one attempt per failover tier
    for tier in ("primary", "backup", "fallback", "fallback-2", "fallback-3"):
        assert f"{tier}:" in str(err)
    assert err.__cause__ is not None


def _healthy_tickers(n: int = 120) -> list:
    return [{"tokenAddress": f"0x{i:03x}", "maxPrice": "1000"} for i in range(n)]


def test_get_tickers_does_not_cache_degraded_payload(monkeypatch):
    _TICKER_PRICES_CACHE.clear()
    api = GMXAPI(chain="arbitrum")

    # First call: degraded payload triggers failover to a healthy one.
    calls = {"n": 0}

    def fake_request(*args, **kwargs):  # noqa: ARG001  # mock signature must accept endpoint/params
        calls["n"] += 1
        if calls["n"] == 1:
            return []  # degraded 200
        return _healthy_tickers()

    monkeypatch.setattr(api, "_make_request", fake_request)

    result = api.get_tickers(use_cache=True)
    assert len(result) == 120  # noqa: PLR2004
    # The degraded payload must not be in the cache.
    assert len(_TICKER_PRICES_CACHE["arbitrum"][0]) == 120  # noqa: PLR2004


def test_get_tickers_serves_stale_snapshot_when_allowed(monkeypatch):
    _TICKER_PRICES_CACHE.clear()
    api = GMXAPI(chain="arbitrum")
    api.retry_config = GMXRetryConfig(allow_stale_prices=True, max_stale_seconds=120.0)

    # Seed a last-known-good snapshot.
    _TICKER_PRICES_CACHE["arbitrum"] = (_healthy_tickers(), time.time())

    def always_fail(*args, **kwargs):  # noqa: ARG001  # mock signature must accept endpoint/params
        raise GMXAPIUnavailable("arbitrum", "/prices/tickers", ("primary: 500",))  # noqa: EM101  # mock exception with fixed arguments

    monkeypatch.setattr(api, "_make_request", always_fail)

    result = api.get_tickers(use_cache=False)
    assert len(result) == 120  # noqa: PLR2004


def test_get_tickers_refuses_stale_snapshot_by_default(monkeypatch):
    _TICKER_PRICES_CACHE.clear()
    api = GMXAPI(chain="arbitrum")
    # Default: allow_stale_prices=False.
    _TICKER_PRICES_CACHE["arbitrum"] = (_healthy_tickers(), time.time())

    def always_fail(*args, **kwargs):  # noqa: ARG001  # mock signature must accept endpoint/params
        raise GMXAPIUnavailable("arbitrum", "/prices/tickers", ("primary: 500",))  # noqa: EM101  # mock exception with fixed arguments

    monkeypatch.setattr(api, "_make_request", always_fail)

    with pytest.raises(GMXAPIUnavailable):
        api.get_tickers(use_cache=False)


def test_get_tickers_raises_when_retry_also_degraded(monkeypatch):
    _TICKER_PRICES_CACHE.clear()
    api = GMXAPI(chain="arbitrum")

    def always_degraded(*args, **kwargs):  # noqa: ARG001
        return []  # degraded 200 every time

    monkeypatch.setattr(api, "_make_request", always_degraded)

    with pytest.raises(GMXInvalidPayloadError):
        api.get_tickers(use_cache=True)


def test_get_tickers_refuses_stale_snapshot_past_max_age(monkeypatch):
    _TICKER_PRICES_CACHE.clear()
    api = GMXAPI(chain="arbitrum")
    api.retry_config = GMXRetryConfig(allow_stale_prices=True, max_stale_seconds=1.0)

    # Seed a snapshot older than max_stale_seconds.
    _TICKER_PRICES_CACHE["arbitrum"] = (_healthy_tickers(), time.time() - 5.0)

    def always_fail(*args, **kwargs):  # noqa: ARG001
        raise GMXAPIUnavailable("arbitrum", "/prices/tickers", ("primary: 500",))  # noqa: EM101

    monkeypatch.setattr(api, "_make_request", always_fail)

    with pytest.raises(GMXAPIUnavailable):
        api.get_tickers(use_cache=False)


def test_fetch_ticker_missing_ticker_raises_exchange_not_available():
    gmx = object.__new__(GMX)

    market = {"info": {"index_token": "0xAAA"}}

    class _Stub:
        def market(self, symbol):  # noqa: PLR6301, ARG002  # stub mimics ccxt API surface
            return market

        def load_markets(self, *a, **k):  # noqa: PLR6301, ARG002  # stub mimics ccxt API surface
            return None

        def api(self):  # noqa: PLR6301  # stub mimics ccxt API surface
            return None

    gmx.market = _Stub().market
    gmx.load_markets = _Stub().load_markets

    api = _Stub()
    api.get_tickers = lambda: []  # no tickers at all -> missing
    gmx.api = api

    with pytest.raises(ExchangeNotAvailable):
        gmx.fetch_ticker("BTC/USDC:USDC")


class _FakeClientResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        # aiohttp.ClientResponse exposes the status as ``status``; mirror it so
        # the driver's status checks work against the mock unchanged.
        self.status = status_code
        self.reason = "mock"
        # ClientResponseError.__str__ dereferences ``request_info.real_url``,
        # so give it a minimal request-info stand-in (same shape aiohttp uses).
        self.request_info = type("_RequestInfo", (), {"real_url": "https://example.com/prices/tickers"})()
        self.history = ()
        self._payload = payload

    async def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:  # noqa: PLR2004  # HTTP error threshold literal
            raise aiohttp.ClientResponseError(self.request_info, self.history, status=self.status_code, message=self.reason)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):  # noqa: ARG002  # mock signature must mirror aiohttp session.get
        self.calls += 1
        if self._responses:
            self._last_response = self._responses.pop(0)
        # A real session keeps serving requests; when the seeded queue is
        # exhausted, keep returning the last response (e.g. "endpoint down").
        return self._last_response

    async def close(self):  # noqa: PLR6301  # stub mimics aiohttp.ClientSession surface
        return None


@pytest.mark.asyncio
async def test_async_fails_over_on_404_without_retry():
    session = _FakeSession(
        [
            _FakeClientResponse(404, []),
            _FakeClientResponse(200, {"ok": True}),
        ]
    )
    result = await async_make_gmx_api_request(
        chain="arbitrum",
        endpoint="/prices/tickers",
        session=session,
        max_retries=3,
        retry_delay=0.01,
    )
    assert result == {"ok": True}
    assert session.calls == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_async_validate_rejects_degraded_payload():
    healthy = [{"tokenAddress": "0x1", "maxPrice": "100"} for _ in range(120)]
    session = _FakeSession(
        [
            _FakeClientResponse(200, []),
            _FakeClientResponse(200, healthy),
        ]
    )
    result = await async_make_gmx_api_request(
        chain="arbitrum",
        endpoint="/prices/tickers",
        session=session,
        max_retries=3,
        retry_delay=0.01,
        validate=lambda p: validate_tickers_payload(p, min_expected_tickers=100),
    )
    assert len(result) == 120  # noqa: PLR2004


@pytest.mark.asyncio
async def test_async_raises_gmxapiunavailable_on_total_failure():
    session = _FakeSession([_FakeClientResponse(500, {})])
    with pytest.raises(GMXAPIUnavailable):
        await async_make_gmx_api_request(
            chain="arbitrum",
            endpoint="/prices/tickers",
            session=session,
            max_retries=1,
            retry_delay=0.01,
        )


@pytest.mark.asyncio
async def test_async_fetch_ticker_missing_ticker_raises_exchange_not_available(monkeypatch):
    gmx = object.__new__(AsyncGMX)
    gmx.chain = "arbitrum"
    gmx.session = None

    market = {"id": "BTC"}

    async def fake_ensure_session():  # noqa: RUF029  # stub replacing the real coroutine
        return None

    async def fake_load_markets(*a, **k):  # noqa: ARG001, RUF029  # stub replacing the real coroutine
        return None

    def fake_market(symbol):  # noqa: ARG001  # stub replacing the real method
        return market

    async def fake_api(*args, **kwargs):  # noqa: ARG001, RUF029  # stub replacing the real coroutine
        return []  # empty ticker list -> missing

    gmx._ensure_session = fake_ensure_session
    gmx.load_markets = fake_load_markets
    gmx.market = fake_market
    monkeypatch.setattr(
        "eth_defi.gmx.ccxt.async_support.exchange.async_make_gmx_api_request",
        fake_api,
    )

    with pytest.raises(ExchangeNotAvailable):
        await gmx.fetch_ticker("BTC/USDC:USDC")
