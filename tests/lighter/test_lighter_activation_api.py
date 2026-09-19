"""Unit tests for Lighter account-activation REST helpers."""

from decimal import Decimal

import pytest
from requests import HTTPError
from requests.exceptions import JSONDecodeError

import eth_defi.lighter.api as lighter_api
from eth_defi.lighter.api import fetch_lighter_account_index, fetch_lighter_api_key, wait_for_lighter_api_key, wait_for_lighter_collateral

LOWEST_ACCOUNT_INDEX = 3
API_KEY_INDEX = 4
EXPECTED_RETRY_CALL_COUNT = 2


class FakeResponse:
    """Minimal successful requests response."""

    def __init__(self, data: dict, status_code: int = 200, *, json_error: bool = False) -> None:
        self.data = data
        self.status_code = status_code
        self.json_error = json_error

    def raise_for_status(self) -> None:
        """Simulate a successful response."""
        if self.status_code >= lighter_api.HTTP_BAD_REQUEST:
            raise HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> dict:
        """Return response JSON."""
        if self.json_error:
            message = "Invalid JSON"
            raise JSONDecodeError(message, "<html>", 0)
        return self.data


class FakeSession:
    """Minimal Lighter session with queued response payloads."""

    api_url = "https://lighter.example"

    def __init__(self, responses: list[dict | tuple[int, dict] | FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        """Record one public API request and return the next payload."""
        assert timeout > 0
        self.calls.append((url, params))
        response = self.responses.pop(0)
        if isinstance(response, FakeResponse):
            return response
        if isinstance(response, tuple):
            status_code, data = response
            return FakeResponse(data, status_code=status_code)
        return FakeResponse(response)


def test_fetch_lighter_account_index_selects_lowest_index() -> None:
    """Account discovery preserves the SDK's lowest-index selection rule."""
    session = FakeSession([{"sub_accounts": [{"index": "9"}, {"index": "3"}]}])

    account_index = fetch_lighter_account_index(session, "0x0000000000000000000000000000000000000001")  # type: ignore[arg-type]

    assert account_index == LOWEST_ACCOUNT_INDEX
    assert session.calls[0][1]["l1_address"] == "0x0000000000000000000000000000000000000001"


def test_fetch_lighter_account_index_treats_not_found_as_pending() -> None:
    """A fresh L1 deposit returns code 21100 until Lighter indexes its account."""
    session = FakeSession([(400, {"code": 21100, "message": "account not found"})])

    assert fetch_lighter_account_index(session, "0x0000000000000000000000000000000000000001") is None  # type: ignore[arg-type]


def test_fetch_lighter_account_index_accepts_null_account_list() -> None:
    """A null Go slice is treated as an account that is not visible yet."""
    session = FakeSession([{"sub_accounts": None}])

    assert fetch_lighter_account_index(session, "0x0000000000000000000000000000000000000001") is None  # type: ignore[arg-type]


def test_fetch_lighter_account_index_preserves_non_json_http_error() -> None:
    """A gateway HTML error is reported as HTTP failure, not malformed JSON."""
    session = FakeSession([FakeResponse({}, status_code=400, json_error=True)])

    with pytest.raises(HTTPError, match="HTTP 400"):
        fetch_lighter_account_index(session, "0x0000000000000000000000000000000000000001")  # type: ignore[arg-type]


def test_wait_for_lighter_collateral_accepts_display_rounding(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0.000010 USDC display shortfall activates the account."""
    session = FakeSession(
        [
            {
                "accounts": [
                    {
                        "account_index": 12,
                        "collateral": "0.999990",
                        "available_balance": "0.999990",
                        "positions": [],
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(lighter_api.time, "sleep", lambda _: None)

    assert wait_for_lighter_collateral(session, 12, Decimal("1"), timeout=1) == Decimal("0.999990")  # type: ignore[arg-type]


def test_wait_for_lighter_api_key_normalises_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-key verification accepts equivalent upper-case and prefixed hex."""
    session = FakeSession(
        [
            {
                "api_keys": [
                    {
                        "api_key_index": 4,
                        "public_key": "AA" * 40,
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(lighter_api.time, "sleep", lambda _: None)

    wait_for_lighter_api_key(session, 12, 4, "0x" + "aa" * 40, timeout=1)  # type: ignore[arg-type]

    assert session.calls == [
        (
            "https://lighter.example/api/v1/apikeys",
            {"account_index": 12, "api_key_index": 4},
        )
    ]


def test_wait_for_lighter_api_key_accepts_camel_case_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-key polling accepts Lighter's alternative camel-case field names."""
    session = FakeSession(
        [
            {
                "apiKeys": [
                    {
                        "apiKeyIndex": API_KEY_INDEX,
                        "publicKey": "aa" * 40,
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(lighter_api.time, "sleep", lambda _: None)

    wait_for_lighter_api_key(session, 12, API_KEY_INDEX, "0x" + "aa" * 40, timeout=1)  # type: ignore[arg-type]


def test_wait_for_lighter_api_key_retries_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A changePubKey transaction returns code 21109 until Lighter indexes it."""
    session = FakeSession(
        [
            (400, {"code": 21109, "message": "api key not found"}),
            {"api_keys": [{"api_key_index": 4, "public_key": "aa" * 40}]},
        ]
    )
    monkeypatch.setattr(lighter_api.time, "sleep", lambda _: None)

    wait_for_lighter_api_key(session, 12, 4, "0x" + "aa" * 40, timeout=1)  # type: ignore[arg-type]

    assert len(session.calls) == EXPECTED_RETRY_CALL_COUNT


def test_wait_for_lighter_api_key_retries_transient_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-key polling survives a server failure within its overall deadline."""
    session = FakeSession(
        [
            FakeResponse({}, status_code=500),
            {"api_keys": [{"api_key_index": API_KEY_INDEX, "public_key": "aa" * 40}]},
        ]
    )
    monkeypatch.setattr(lighter_api.time, "sleep", lambda _: None)

    wait_for_lighter_api_key(session, 12, API_KEY_INDEX, "0x" + "aa" * 40, timeout=1)  # type: ignore[arg-type]

    assert len(session.calls) == EXPECTED_RETRY_CALL_COUNT


def test_fetch_lighter_api_key_accepts_null_key_list() -> None:
    """A null Go slice is treated as a key that is not visible yet."""
    session = FakeSession([{"api_keys": None}])

    assert fetch_lighter_api_key(session, 12, API_KEY_INDEX) is None  # type: ignore[arg-type]
