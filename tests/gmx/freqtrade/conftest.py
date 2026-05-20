"""Shared fixtures for the freqtrade-adapter test module."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest


@pytest.fixture
def fake_order_factory() -> Callable[..., dict]:
    """Build a CCXT-shaped order dict with controllable type / timestamp / status."""

    def _factory(
        order_type: str = "market",
        status: str = "open",
        timestamp_ms: int | None = None,
        order_id: str = "0xabc",
        amount: float = 1.0,
    ) -> dict:
        if timestamp_ms is None:
            timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return {
            "id": order_id,
            "type": order_type,
            "status": status,
            "timestamp": timestamp_ms,
            "amount": amount,
            "filled": 0.0,
            "remaining": amount,
            "info": {},
        }

    return _factory
