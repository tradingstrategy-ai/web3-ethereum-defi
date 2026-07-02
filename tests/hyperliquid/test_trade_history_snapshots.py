"""Synthetic tests for Hyperliquid snapshot persistence."""

import datetime
import shutil
from pathlib import Path

from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase


FIXTURE_DB = Path(__file__).parent / "fixtures" / "trade-history-sample.duckdb"
ADDRESS = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"
SNAPSHOT_TIME = datetime.datetime(2026, 3, 22, 12, 0, 0)


class DummyResponse:
    """Minimal response stub for snapshot tests."""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        """Pretend the response succeeded."""

    def json(self):
        """Return the mocked JSON payload."""
        return self.payload


class DummySession:
    """Session stub keyed by Hyperliquid ``type`` payloads."""

    def __init__(self, payloads: dict[str, object], failures: dict[str, Exception] | None = None):
        self.payloads = payloads
        self.failures = failures or {}
        self.calls: list[dict] = []

    def post_info(self, payload: dict, timeout: float = 30.0) -> DummyResponse:
        """Return the configured payload for the requested type."""
        self.calls.append(payload)
        request_type = payload["type"]
        if request_type == "activeAssetData":
            request_type = f"activeAssetData:{payload['coin']}"

        failure = self.failures.get(request_type)
        if failure is not None:
            raise failure

        return DummyResponse(self.payloads[request_type])


def _seed_open_btc_trade(db: HyperliquidTradeHistoryDatabase, address: str) -> None:
    """Insert minimal local trade history for one open BTC trade."""
    db._insert_fills_batch(  # noqa: SLF001
        address,
        [
            (
                address,
                1,
                int(datetime.datetime(2026, 3, 20, 10, 0, 0).timestamp() * 1000),
                "BTC",
                0,
                1.0,
                50000.0,
                0.0,
                0.0,
                1.5,
                111,
            )
        ],
    )
    db._insert_funding_batch(  # noqa: SLF001
        address,
        [
            (
                address,
                int(datetime.datetime(2026, 3, 21, 10, 0, 0).timestamp() * 1000),
                "BTC",
                -2.0,
                1.0,
                0.0001,
            )
        ],
    )


def _make_payloads() -> dict[str, object]:
    """Build deterministic snapshot payloads for tests."""
    return {
        "clearinghouseState": {
            "crossMarginSummary": {
                "accountValue": "1000",
                "totalNtlPos": "100",
                "totalRawUsd": "990",
                "totalMarginUsed": "50",
            },
            "withdrawable": "940",
            "assetPositions": [
                {
                    "type": "oneWay",
                    "position": {
                        "coin": "BTC",
                        "szi": "1",
                        "entryPx": "50000",
                        "unrealizedPnl": "25",
                        "marginUsed": "50",
                        "positionValue": "50025",
                        "liquidationPx": "42000",
                        "returnOnEquity": "0.5",
                        "maxLeverage": 20,
                        "leverage": {"type": "cross", "value": 10},
                        "cumFunding": {
                            "allTime": "12",
                            "sinceOpen": "5",
                            "sinceChange": "-1",
                        },
                    },
                }
            ],
        },
        "activeAssetData:BTC": {
            "user": ADDRESS,
            "coin": "BTC",
            "leverage": {"type": "cross", "value": 10},
            "maxTradeSzs": ["10", "20"],
            "availableToTrade": ["1000", "900"],
            "markPx": "50025",
        },
        "openOrders": [
            {
                "coin": "BTC",
                "side": "B",
                "limitPx": "50010",
                "sz": "1",
                "origSz": "1",
                "oid": 99,
                "timestamp": 1770000000000,
                "triggerCondition": "N/A",
                "isTrigger": False,
                "triggerPx": "0",
                "children": [],
                "isPositionTpsl": False,
                "reduceOnly": False,
                "orderType": "Limit",
                "tif": "Gtc",
                "cloid": None,
            }
        ],
        "frontendOpenOrders": [
            {
                "order": {
                    "coin": "BTC",
                    "side": "A",
                    "limitPx": "50020",
                    "sz": "2",
                    "origSz": "2",
                    "oid": 100,
                    "timestamp": 1770000000100,
                    "triggerCondition": "N/A",
                    "isTrigger": False,
                    "triggerPx": "0",
                    "children": [],
                    "isPositionTpsl": False,
                    "reduceOnly": True,
                    "orderType": "Limit",
                    "tif": "Gtc",
                    "cloid": "abc-123",
                },
                "status": "open",
                "statusTimestamp": 1770000000200,
            }
        ],
        "historicalOrders": [
            {
                "order": {
                    "coin": "BTC",
                    "side": "B",
                    "limitPx": "49990",
                    "sz": "0",
                    "origSz": "1",
                    "oid": 77,
                    "timestamp": 1769990000000,
                    "triggerCondition": "N/A",
                    "isTrigger": False,
                    "triggerPx": "0",
                    "children": [],
                    "isPositionTpsl": False,
                    "reduceOnly": False,
                    "orderType": "Limit",
                    "tif": "Ioc",
                    "cloid": None,
                },
                "status": "filled",
                "statusTimestamp": 1769990000000,
            }
        ],
        "userTwapSliceFills": [{"coin": "BTC", "px": "50000", "sz": "0.5"}],
    }


def test_capture_account_snapshots_persists_raw_and_materialised_rows(tmp_path):
    """Snapshot capture stores raw payloads and materialised position/order/trade rows."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(ADDRESS, label="Growi HF", is_vault=True)
        _seed_open_btc_trade(db, ADDRESS)
        session = DummySession(_make_payloads())

        result = db.capture_account_snapshots(
            session,
            ADDRESS,
            is_vault=True,
            label="Growi HF",
            snapshot_time=SNAPSHOT_TIME,
        )

        assert result == {"open_positions": 1, "open_trades": 1, "open_orders": 1}

        runs = db.get_snapshot_runs(ADDRESS)
        assert len(runs) == 1
        assert runs[0]["open_position_count"] == 1
        assert runs[0]["open_trade_count"] == 1
        assert runs[0]["open_order_count"] == 1
        assert runs[0]["historical_order_count"] == 1
        assert runs[0]["twap_slice_fill_count"] == 1

        source = db.get_snapshot_source(ADDRESS, "clearinghouseState")
        assert source is not None
        assert source["status"] == "ok"
        assert source["item_count"] == 1
        assert '"coin":"BTC"' in source["payload_json"]

        active_asset_source = db.get_snapshot_source(ADDRESS, "activeAssetData:BTC")
        assert active_asset_source is not None
        assert active_asset_source["status"] == "ok"

        positions = db.get_open_position_snapshots(ADDRESS)
        assert len(positions) == 1
        assert positions[0]["coin"] == "BTC"
        assert positions[0]["mark_px"] == 50025.0

        orders = db.get_open_order_snapshots(ADDRESS)
        assert len(orders) == 1
        assert orders[0]["source"] == "frontendOpenOrders"
        assert orders[0]["oid"] == 100
        assert orders[0]["reduce_only"] is True

        trades = db.get_open_trade_snapshots(ADDRESS)
        assert len(trades) == 1
        assert trades[0]["coin"] == "BTC"
        assert trades[0]["unrealised_pnl"] == 25.0
        assert trades[0]["funding_pnl"] == -2.0
    finally:
        db.close()


def test_capture_account_snapshots_falls_back_to_open_orders_and_records_errors(tmp_path):
    """Snapshot capture uses openOrders when frontendOpenOrders fails."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(ADDRESS, label="Growi HF", is_vault=True)
        _seed_open_btc_trade(db, ADDRESS)
        session = DummySession(
            _make_payloads(),
            failures={"frontendOpenOrders": RuntimeError("frontend unavailable")},
        )

        result = db.capture_account_snapshots(
            session,
            ADDRESS,
            is_vault=True,
            label="Growi HF",
            snapshot_time=SNAPSHOT_TIME,
        )

        assert result["open_orders"] == 1

        source = db.get_snapshot_source(ADDRESS, "frontendOpenOrders")
        assert source is not None
        assert source["status"] == "error"
        assert "frontend unavailable" in source["error_message"]

        orders = db.get_open_order_snapshots(ADDRESS)
        assert len(orders) == 1
        assert orders[0]["source"] == "openOrders"
        assert orders[0]["oid"] == 99
    finally:
        db.close()


def test_snapshot_schema_migration_preserves_existing_trade_history(tmp_path):
    """Opening an existing DB fixture adds snapshot tables without changing old counts."""
    test_db = tmp_path / "trade-history-sample.duckdb"
    shutil.copy2(FIXTURE_DB, test_db)

    db = HyperliquidTradeHistoryDatabase(test_db)
    try:
        totals = db.get_total_row_counts()
        assert totals["fills"] == 400
        assert totals["funding"] == 156
        assert totals["ledger"] == 526
        assert totals["snapshot_runs"] == 0
        assert totals["snapshot_sources"] == 0
        assert totals["open_positions"] == 0
        assert totals["open_trades"] == 0
        assert totals["open_orders"] == 0
    finally:
        db.close()
