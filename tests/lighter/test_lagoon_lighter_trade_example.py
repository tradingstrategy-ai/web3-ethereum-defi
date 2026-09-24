"""Tests for the Lagoon + Lighter API-key trading tutorial."""

import asyncio
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LighterAccountSetup

API_KEY_INDEX = 4
OPEN_BASE_AMOUNT = 50
FILLED_BASE_AMOUNT = 49


def load_tutorial_module() -> ModuleType:
    """Load the hyphenated tutorial script as a test module."""
    script_path = Path(__file__).parents[2] / "scripts" / "lagoon" / "lagoon-lighter-trade-example.py"
    spec = importlib.util.spec_from_file_location("lagoon_lighter_trade_example", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tutorial = load_tutorial_module()


class FakeSignerClient:
    """Minimal official-SDK signer client mock."""

    instance = None

    def __init__(self, **kwargs: Any) -> None:
        self.constructor_kwargs = kwargs
        self.orders: list[dict] = []
        self.closed = False
        type(self).instance = self

    @staticmethod
    def check_client() -> None:
        """Accept the registered mock API key."""
        return None

    async def create_market_order_limited_slippage(self, **kwargs: Any) -> tuple[SimpleNamespace, SimpleNamespace, None]:
        """Record one signed market order."""
        self.orders.append(kwargs)
        return SimpleNamespace(order="signed"), SimpleNamespace(tx_hash="0x1234"), None

    async def close(self) -> None:
        """Record SDK client cleanup."""
        self.closed = True


def test_calculate_eth_trade_plan_honours_live_market_precision() -> None:
    """Trade sizing rounds up to the ETH base minimum and leaves collateral buffer."""
    market = SimpleNamespace(
        symbol="ETH",
        market_id=0,
        market_type="perp",
        min_quote_amount="10.000000",
        min_base_amount="0.0050",
        last_trade_price="3000.00",
        size_decimals=4,
        supported_size_decimals=4,
    )

    plan = tutorial.calculate_eth_trade_plan(market)

    assert plan.base_size == Decimal("0.0050")
    assert plan.base_amount == OPEN_BASE_AMOUNT
    assert plan.position_usdc == Decimal("15.000000")
    assert plan.deposit_usdc == Decimal("20.000000")


def test_calculate_eth_trade_plan_rejects_silent_position_increase() -> None:
    """An explicit position below the conservative floor fails visibly."""
    market = SimpleNamespace(
        symbol="ETH",
        market_id=0,
        market_type="perp",
        min_quote_amount="10.000000",
        min_base_amount="0.0050",
        last_trade_price="3000.00",
        size_decimals=4,
        supported_size_decimals=4,
    )

    with pytest.raises(ValueError, match="conservative sizing floor"):
        tutorial.calculate_eth_trade_plan(market, position_usdc=Decimal("5"))


def test_calculate_eth_trade_plan_rejects_unsupported_precision() -> None:
    """SDK base precision must fit the integer order-size scale."""
    market = SimpleNamespace(
        symbol="ETH",
        market_id=0,
        market_type="perp",
        min_quote_amount="10",
        min_base_amount="0.005",
        last_trade_price="3000",
        size_decimals=4,
        supported_size_decimals=5,
    )

    with pytest.raises(ValueError, match="supported_size_decimals"):
        tutorial.calculate_eth_trade_plan(market)


def test_get_signed_eth_position() -> None:
    """Public account positions preserve Lighter's direction sign."""
    account = {
        "positions": [
            {"market_id": 1, "position": "0.2", "sign": 1},
            {"market_id": 0, "position": "0.0050", "sign": -1},
        ]
    }

    assert tutorial.get_signed_eth_position(account) == Decimal("-0.0050")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "positions", "expected"),
    [
        ("open", ("0", "0.005"), Decimal("0.005")),
        ("flat", ("0.005", "0"), Decimal(0)),
    ],
)
async def test_wait_for_eth_position_polls_public_account(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    positions: tuple[str, str],
    expected: Decimal,
) -> None:
    """Position polling waits for either an open long or a flat account."""
    responses = iter({"positions": [{"market_id": 0, "position": position, "sign": 0}]} for position in positions)
    monkeypatch.setattr(tutorial, "fetch_lighter_account_by_index", lambda *_args: next(responses))

    result = await tutorial.wait_for_eth_position(object(), 123, open_position=target == "open", poll_seconds=0)

    assert result == expected


@pytest.mark.asyncio
async def test_trade_eth_roundtrip_uses_deployment_api_key_and_closes_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock SDK signs the open and reduce-only close with the deployed API key."""
    positions = iter((Decimal("0.0049"), Decimal(0)))

    async def fake_wait_for_eth_position(*_args: Any, **_kwargs: Any) -> Decimal:
        await asyncio.sleep(0)
        return next(positions)

    monkeypatch.setattr(tutorial, "wait_for_eth_position", fake_wait_for_eth_position)
    lighter = SimpleNamespace(SignerClient=FakeSignerClient)
    setup = LighterAccountSetup(
        account_index=123,
        api_key_index=API_KEY_INDEX,
        private_key="0x" + "01" * 40,
        public_key="0x" + "02" * 40,
        activation_amount=Decimal("1"),
        deposit_tx_hash="0xdeposit",
        change_pubkey_tx_hash="0xchange",
        observed_collateral=Decimal("1"),
    )
    plan = tutorial.EthTradePlan(
        deposit_usdc=Decimal("20"),
        position_usdc=Decimal("15"),
        base_amount=OPEN_BASE_AMOUNT,
        base_size=Decimal("0.0050"),
        size_decimals=4,
        min_quote_amount=Decimal("10"),
        min_base_amount=Decimal("0.0050"),
    )

    await tutorial.trade_eth_roundtrip(lighter, object(), setup, plan, Decimal("0.02"))

    client = FakeSignerClient.instance
    assert client.constructor_kwargs == {
        "url": tutorial.LIGHTER_API_URL,
        "account_index": 123,
        "api_private_keys": {API_KEY_INDEX: setup.private_key},
    }
    assert client.orders[0]["base_amount"] == OPEN_BASE_AMOUNT
    assert client.orders[0]["is_ask"] is False
    assert client.orders[0]["api_key_index"] == API_KEY_INDEX
    assert client.orders[1]["base_amount"] == FILLED_BASE_AMOUNT
    assert client.orders[1]["is_ask"] is True
    assert client.orders[1]["reduce_only"] is True
    assert client.orders[1]["api_key_index"] == API_KEY_INDEX
    assert client.closed is True
