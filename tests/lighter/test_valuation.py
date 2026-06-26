"""Integration tests for Lighter account valuation."""

import os
import site
import sys
from decimal import Decimal
from importlib import machinery, util
from types import ModuleType

import pytest

from eth_defi.lighter.constants import LIGHTER_API_URL
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.valuation import fetch_lighter_account_by_index, fetch_lighter_total_equity, parse_lighter_account_equity

pytestmark = pytest.mark.timeout(60)

ACCOUNT_INDEX = 731323
API_KEY_INDEX = 4
POSITION_COUNT = 2
ROUNDING_TOLERANCE = Decimal("0.01")


class FakeResponse:
    """Minimal response object for valuation unit tests."""

    def __init__(self, data: dict):
        self.data = data

    def raise_for_status(self) -> None:
        """Simulate a successful HTTP response."""

    def json(self) -> dict:
        """Return fake JSON payload."""
        return self.data


class FakeSession:
    """Minimal Lighter session stand-in."""

    api_url = "https://lighter.example"

    def __init__(self, data: dict):
        self.data = data
        self.calls: list[tuple[str, dict, float]] = []

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        """Record the call and return fake account data."""
        self.calls.append((url, params, timeout))
        return FakeResponse(self.data)


def make_account() -> dict:
    """Create a realistic Lighter account response item."""
    return {
        "account_index": ACCOUNT_INDEX,
        "collateral": "1000.50",
        "available_balance": "800.25",
        "cross_initial_margin_requirement": "120.00",
        "cross_maintenance_margin_requirement": "60.00",
        "total_asset_value": "1012.75",
        "positions": [
            {"unrealized_pnl": "10.00"},
            {"unrealizedPnl": "2.25"},
        ],
    }


def _require_env(name: str) -> str:
    """Read an environment variable or skip the integration test."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"Set {name} to run Lighter valuation integration tests")
    return value


def _import_lighter_sdk() -> ModuleType:
    """Import the installed Lighter SDK, avoiding ``tests/lighter`` shadowing."""
    existing = sys.modules.get("lighter")
    if existing is not None and hasattr(existing, "SignerClient"):
        return existing

    for package_path in site.getsitepackages():
        spec = machinery.PathFinder.find_spec("lighter", [package_path])
        if spec is None or spec.loader is None:
            continue
        if not spec.origin or "site-packages" not in spec.origin:
            continue

        module = util.module_from_spec(spec)
        sys.modules["lighter"] = module
        spec.loader.exec_module(module)
        if hasattr(module, "SignerClient"):
            return module

    pytest.skip("Install the Lighter SDK to run Lighter valuation integration tests")


def test_parse_lighter_account_equity() -> None:
    """Lighter account parser returns Decimal NAV components."""
    equity = parse_lighter_account_equity(make_account())

    assert equity.account_index == ACCOUNT_INDEX
    assert equity.collateral == Decimal("1000.50")
    assert equity.unrealised_pnl == Decimal("12.25")
    assert equity.total_asset_value == Decimal("1012.75")
    assert equity.available_balance == Decimal("800.25")
    assert equity.initial_margin_requirement == Decimal("120.00")
    assert equity.maintenance_margin_requirement == Decimal("60.00")
    assert equity.position_count == POSITION_COUNT
    assert equity.get_total() == Decimal("1012.75")
    assert equity.calculate_total_from_parts() == Decimal("1012.75")


def test_parse_lighter_account_equity_falls_back_to_index_and_cross_asset_value() -> None:
    """Parser accepts alternative account and NAV fields from Lighter responses."""
    account = make_account()
    account["index"] = account.pop("account_index")
    account["cross_asset_value"] = account.pop("total_asset_value")

    equity = parse_lighter_account_equity(account)

    assert equity.account_index == ACCOUNT_INDEX
    assert equity.get_total() == Decimal("1012.75")


def test_parse_lighter_account_equity_falls_back_to_calculated_total() -> None:
    """Missing total asset value falls back to collateral plus unrealised PnL."""
    account = make_account()
    del account["total_asset_value"]

    equity = parse_lighter_account_equity(account)

    assert equity.get_total() == Decimal("1012.75")


def test_fetch_lighter_account_by_index() -> None:
    """Fetcher calls the Lighter account endpoint by index."""
    session = FakeSession({"accounts": [make_account()]})

    account = fetch_lighter_account_by_index(session, account_index=ACCOUNT_INDEX, timeout=5.0)  # type: ignore[arg-type]

    assert account["account_index"] == ACCOUNT_INDEX
    assert session.calls == [
        (
            "https://lighter.example/api/v1/account",
            {"by": "index", "value": str(ACCOUNT_INDEX)},
            5.0,
        )
    ]


def test_fetch_lighter_account_by_index_rejects_empty_response() -> None:
    """Fetcher raises if the Lighter API returns no accounts."""
    session = FakeSession({"accounts": []})

    with pytest.raises(ValueError, match="No Lighter account data returned"):
        fetch_lighter_account_by_index(session, account_index=ACCOUNT_INDEX)  # type: ignore[arg-type]


def test_fetch_lighter_account_by_index_rejects_mismatched_response() -> None:
    """Fetcher raises if the Lighter API returns a different account."""
    account = make_account()
    account["account_index"] = ACCOUNT_INDEX + 1
    session = FakeSession({"accounts": [account]})

    with pytest.raises(ValueError, match="returned account index"):
        fetch_lighter_account_by_index(session, account_index=ACCOUNT_INDEX)  # type: ignore[arg-type]


def test_fetch_lighter_account_by_index_rejects_missing_response_index() -> None:
    """Fetcher raises if the account object has no account index field."""
    account = make_account()
    del account["account_index"]
    session = FakeSession({"accounts": [account]})

    with pytest.raises(ValueError, match="account_index or index"):
        fetch_lighter_account_by_index(session, account_index=ACCOUNT_INDEX)  # type: ignore[arg-type]


def test_parse_lighter_account_equity_rejects_bad_decimal() -> None:
    """Malformed decimal fields raise a clear error."""
    account = make_account()
    account["collateral"] = "not-a-number"

    with pytest.raises(ValueError, match="collateral"):
        parse_lighter_account_equity(account)


def test_parse_lighter_account_equity_rejects_non_finite_decimal() -> None:
    """Non-finite decimal fields raise a clear error."""
    account = make_account()
    account["total_asset_value"] = "NaN"

    with pytest.raises(ValueError, match="finite decimal"):
        parse_lighter_account_equity(account)


@pytest.mark.asyncio
async def test_fetch_lighter_total_equity_with_registered_api_key() -> None:
    """Fetch NAV for a real Lighter account with a registered API key."""
    lighter = _import_lighter_sdk()

    account_index = int(_require_env("LIGHTER_TEST_ACCOUNT_INDEX"))
    api_private_key = _require_env("LIGHTER_TEST_ACCOUNT_API_KEY")
    api_key_index = int(os.environ.get("LIGHTER_TEST_ACCOUNT_API_KEY_INDEX", str(API_KEY_INDEX)))

    client = lighter.SignerClient(
        url=LIGHTER_API_URL,
        account_index=account_index,
        api_private_keys={api_key_index: api_private_key},
    )
    try:
        err = client.check_client()
        assert err is None
    finally:
        await client.close()

    session = create_lighter_session()
    try:
        equity = fetch_lighter_total_equity(session, account_index)
    finally:
        session.close()

    assert equity.account_index == account_index
    assert equity.get_total() >= Decimal("1")
    assert equity.collateral >= Decimal("1")
    assert equity.available_balance >= Decimal(0)
    assert equity.calculate_total_from_parts() == pytest.approx(equity.get_total(), abs=ROUNDING_TOLERANCE)
