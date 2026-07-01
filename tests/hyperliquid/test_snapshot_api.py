"""Offline unit tests for Hyperliquid snapshot API readers."""

from decimal import Decimal

from eth_defi.hyperliquid.api import (
    fetch_active_asset_data_raw,
    fetch_frontend_open_orders_raw,
    fetch_historical_orders_raw,
    fetch_open_orders_raw,
    fetch_perp_clearinghouse_state,
    fetch_user_twap_slice_fills_raw,
)


class DummyResponse:
    """Minimal response stub for API reader tests."""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        """Pretend the response succeeded."""

    def json(self):
        """Return the mocked JSON payload."""
        return self.payload


class DummySession:
    """Minimal session stub that records requests."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[dict, float]] = []

    def post_info(self, payload: dict, timeout: float = 30.0) -> DummyResponse:
        """Record the request and return the configured payload."""
        self.calls.append((payload, timeout))
        return DummyResponse(self.payload)


def test_fetch_open_orders_raw_returns_payload_unchanged():
    """openOrders raw reader passes through the JSON response unchanged."""
    payload = [{"coin": "BTC", "side": "B"}]
    session = DummySession(payload)

    result = fetch_open_orders_raw(session, "0xabc", timeout=12.5)

    assert result == payload
    assert session.calls == [({"type": "openOrders", "user": "0xabc"}, 12.5)]


def test_fetch_frontend_open_orders_raw_returns_payload_unchanged():
    """frontendOpenOrders raw reader passes through the JSON response unchanged."""
    payload = [{"order": {"coin": "ETH", "side": "A"}, "status": "open"}]
    session = DummySession(payload)

    result = fetch_frontend_open_orders_raw(session, "0xabc")

    assert result == payload
    assert session.calls == [({"type": "frontendOpenOrders", "user": "0xabc"}, 10.0)]


def test_fetch_historical_orders_raw_returns_payload_unchanged():
    """historicalOrders raw reader passes through the JSON response unchanged."""
    payload = [{"order": {"coin": "SOL"}, "status": "filled"}]
    session = DummySession(payload)

    result = fetch_historical_orders_raw(session, "0xabc")

    assert result == payload
    assert session.calls == [({"type": "historicalOrders", "user": "0xabc"}, 10.0)]


def test_fetch_user_twap_slice_fills_raw_returns_payload_unchanged():
    """userTwapSliceFills raw reader passes through the JSON response unchanged."""
    payload = [{"coin": "BTC", "px": "50000", "sz": "1"}]
    session = DummySession(payload)

    result = fetch_user_twap_slice_fills_raw(session, "0xabc")

    assert result == payload
    assert session.calls == [({"type": "userTwapSliceFills", "user": "0xabc"}, 10.0)]


def test_fetch_active_asset_data_raw_returns_payload_unchanged():
    """activeAssetData raw reader passes through the JSON response unchanged."""
    payload = {
        "user": "0xabc",
        "coin": "BTC",
        "leverage": {"type": "cross", "value": 20},
        "maxTradeSzs": ["10", "20"],
        "availableToTrade": ["1000", "2000"],
        "markPx": "50000",
    }
    session = DummySession(payload)

    result = fetch_active_asset_data_raw(session, "0xabc", "BTC", timeout=5.0)

    assert result == payload
    assert session.calls == [({"type": "activeAssetData", "user": "0xabc", "coin": "BTC"}, 5.0)]


def test_fetch_perp_clearinghouse_state_parses_snapshot_fields():
    """clearinghouseState parser keeps the richer position fields we rely on."""
    payload = {
        "crossMarginSummary": {
            "accountValue": "1000",
            "totalNtlPos": "250",
            "totalRawUsd": "980",
            "totalMarginUsed": "50",
        },
        "withdrawable": "950",
        "assetPositions": [
            {
                "type": "oneWay",
                "position": {
                    "coin": "BTC",
                    "szi": "1.25",
                    "entryPx": "50000",
                    "unrealizedPnl": "25",
                    "marginUsed": "50",
                    "positionValue": "62500",
                    "liquidationPx": "42000",
                    "returnOnEquity": "0.5",
                    "maxLeverage": 40,
                    "leverage": {"type": "cross", "value": 20},
                    "cumFunding": {
                        "allTime": "10.5",
                        "sinceOpen": "3.25",
                        "sinceChange": "-0.75",
                    },
                },
            }
        ],
    }
    session = DummySession(payload)

    state = fetch_perp_clearinghouse_state(session, "0xabc")

    assert state.margin_summary.account_value == Decimal("1000")
    assert state.withdrawable == Decimal("950")
    assert len(state.asset_positions) == 1

    position = state.asset_positions[0]
    assert position.coin == "BTC"
    assert position.position_type == "oneWay"
    assert position.leverage_type == "cross"
    assert position.leverage_value == 20
    assert position.max_leverage == 40
    assert position.return_on_equity == Decimal("0.5")
    assert position.cumulative_funding_all_time == Decimal("10.5")
    assert position.cumulative_funding_since_open == Decimal("3.25")
    assert position.cumulative_funding_since_change == Decimal("-0.75")
