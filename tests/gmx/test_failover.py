from unittest.mock import patch

import pytest
import requests

from eth_defi.gmx.retry import (
    GMXAPIUnavailable,
    GMXRetryConfig,
    is_retryable_http_status,
    make_gmx_api_request,
)
from eth_defi.gmx.ticker_validation import validate_tickers_payload


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
    primary = _FakeResponse(404, [])
    backup = _FakeResponse(200, {"ok": True})

    with patch("eth_defi.gmx.retry.requests.get", side_effect=[primary, backup]) as get:
        result = make_gmx_api_request(
            chain="arbitrum",
            endpoint="/prices/tickers",
            retry_config=GMXRetryConfig.create_test_config(),
        )

    assert result == {"ok": True}
    # Primary is hit exactly once (no retry/backoff on 404), backup once.
    assert get.call_count == 2  # noqa: PLR2004


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
