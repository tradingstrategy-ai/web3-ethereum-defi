"""The ccxt close path must forward the configured PnL-payout direction.

Regression for a gap found while reviewing the GMX PnL-token fix
(``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``): ``OrderParams``
and ``DecreaseOrder.create_decrease_order()`` accept
``decrease_position_swap_type`` / ``should_unwrap_native_token``, but neither
``_convert_ccxt_to_gmx_params()`` nor the ``close_kwargs`` it feeds into
``trader.close_position()`` ever read them from the caller's ``params`` --
so a ccxt caller had no way to configure the payout direction at all; every
close silently used the hardcoded default regardless of what was passed.

Uses the ``ccxt_gmx_fork_open_close`` fixture -- a real, wallet-backed GMX
instance on an isolated Anvil fork, the same fixture
``test_execution_buffer_forwarding.py`` uses to intercept opens. A view-only
instance (no wallet) has ``self.trader is None`` -- there is nothing to
build orders with -- so it cannot be used here. ``_execute_close_with_position()``
is documented as extracted from ``create_order()`` specifically so this
assembly logic can be exercised without broadcasting anything: the trader's
``close_position`` is intercepted via ``monkeypatch`` on the real object
(matching ``test_execution_buffer_forwarding.py``'s pattern), not replaced
with a mock, so nothing is silently faked.
"""

import asyncio
import os

import pytest
from eth_typing import HexAddress
from web3.main import to_checksum_address

from eth_defi.gmx.ccxt.async_support import exchange as async_exchange
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.constants import DECREASE_POSITION_SWAP_TYPES, PRECISION
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.order.sltp_order import SLTPEntry, SLTPOrder
from eth_defi.gmx.valuation import _native_wrapped_token_address  # noqa: PLC2701


class _Intercepted(Exception):
    """Raised by the stub to stop before anything is signed or broadcast."""

    def __init__(self, kwargs: dict):
        super().__init__("intercepted")
        self.kwargs = kwargs


def _make_gmx_params(gmx: GMX, symbol: str, market_key: str, **overrides) -> dict:
    """Build a ``gmx_params`` dict as ``_convert_ccxt_to_gmx_params`` would produce.

    :param gmx: Live GMX CCXT exchange instance (markets already loaded).
    :param symbol: CCXT unified symbol (e.g. ``ETH/USDC:USDC``).
    :param market_key: Pool address.
    :param overrides: Additional keys to merge in (e.g. the two PnL-payout fields).
    :return: Parameters dict compatible with ``_execute_close_with_position``.
    """
    market = gmx.markets[symbol]
    base = market["base"]
    index_token = market.get("info", {}).get("index_token", "")
    params = {
        "market_symbol": base,
        "collateral_symbol": "USDC",
        "start_token_symbol": "USDC",
        "is_long": True,
        "size_delta_usd": 100.0,
        "leverage": 2.0,
        "slippage_percent": 0.003,
        "execution_buffer": 1.3,
        "auto_cancel": False,
        "market_key": market_key,
        "index_token_address": index_token,
        "_gmx_position": None,
        "_resolved_market_info": {},
        "_collateral_explicitly_set": False,
    }
    params.update(overrides)
    return params


def _make_position(market: str) -> dict:
    """Build a minimal on-chain position dict as ``GetOpenPositions`` returns.

    :param market: Authoritative onchain pool address.
    :return: Position dict compatible with ``_execute_close_with_position``.
    """
    return {
        "market": market,
        "collateral_token": "USDC",
        "is_long": True,
        "position_size": 200.0,
        "position_size_usd_raw": 200 * 10**30,
        "initial_collateral_amount_usd": 100.0,
        "leverage": 2.0,
    }


def test_close_forwards_configured_swap_type(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """A non-default ``decrease_position_swap_type`` must reach ``trader.close_position``."""
    gmx = ccxt_gmx_fork_open_close
    symbol = "ETH/USDC:USDC"
    market_key = gmx.markets[symbol]["info"]["market_token"]

    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        raise _Intercepted(kwargs)

    monkeypatch.setattr(gmx.trader, "close_position", _stub)

    gmx_params = _make_gmx_params(
        gmx,
        symbol,
        market_key,
        decrease_position_swap_type=DECREASE_POSITION_SWAP_TYPES["no_swap"],
        should_unwrap_native_token=True,
    )
    position_to_close = _make_position(market=market_key)

    with pytest.raises(_Intercepted):
        gmx._execute_close_with_position(
            symbol=symbol,
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert captured.get("decrease_position_swap_type") == DECREASE_POSITION_SWAP_TYPES["no_swap"]
    assert captured.get("should_unwrap_native_token") is True


def test_close_omits_swap_type_when_caller_does_not_configure_one(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """When the caller configures nothing, ``close_kwargs`` must not force a value.

    ``DecreaseOrder.create_decrease_order()`` supplies its own default
    (``swap_pnl_token_to_collateral_token`` / ``should_unwrap_native_token=False``)
    when the keyword is absent entirely. The forwarding must be conditional,
    not an unconditional pass-through of a possibly-stale default, so that
    default stays the single source of truth.
    """
    gmx = ccxt_gmx_fork_open_close
    symbol = "ETH/USDC:USDC"
    market_key = gmx.markets[symbol]["info"]["market_token"]

    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        raise _Intercepted(kwargs)

    monkeypatch.setattr(gmx.trader, "close_position", _stub)

    gmx_params = _make_gmx_params(gmx, symbol, market_key)
    position_to_close = _make_position(market=market_key)

    with pytest.raises(_Intercepted):
        gmx._execute_close_with_position(
            symbol=symbol,
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert "decrease_position_swap_type" not in captured
    assert "should_unwrap_native_token" not in captured


def test_bundled_sltp_forwards_configured_pnl_payout_options(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """A configured swap type must reach the bundled SL/TP's ``SLTPOrder`` instance too.

    Separate gap from the plain-close path above: bundled SL/TP orders (a
    position opened together with stop-loss/take-profit in one multicall)
    are assembled by ``_create_order_with_sltp()``, which constructs its own
    ``SLTPOrder`` directly -- a code path independent of ``close_kwargs`` /
    ``trader.close_position()``. Before this fix, that constructor call never
    read ``decrease_position_swap_type`` / ``should_unwrap_native_token``
    from the caller's params at all, so a bundled take-profit always used
    ``SLTPOrder``'s hardcoded defaults regardless of what was configured.

    Intercepts ``SLTPOrder.create_increase_order_with_sltp()`` -- called
    immediately after construction, before anything is signed -- to read
    back what the constructor actually received.
    """
    gmx = ccxt_gmx_fork_open_close
    captured: dict = {}

    def _stop_after_sltp_construction(sltp_order, *_args, **_kwargs):
        captured["decrease_position_swap_type"] = sltp_order.decrease_position_swap_type
        captured["should_unwrap_native_token"] = sltp_order.should_unwrap_native_token
        raise _Intercepted(captured)

    monkeypatch.setattr(SLTPOrder, "create_increase_order_with_sltp", _stop_after_sltp_construction)

    with pytest.raises(_Intercepted):
        gmx.create_order(
            "ETH/USDC:USDC",
            "market",
            "buy",
            0,
            params={
                "size_usd": 10.0,
                "leverage": 2.0,
                "collateral_symbol": "USDC",
                "takeProfit": {"triggerPercent": 0.10, "closePercent": 1.0},
                "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
                "should_unwrap_native_token": True,
            },
        )

    assert captured == {
        "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
        "should_unwrap_native_token": True,
    }


def test_bundled_sltp_omits_swap_type_when_caller_does_not_configure_one(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """When the caller configures nothing, the bundled SL/TP still gets ``SLTPOrder``'s own defaults."""
    gmx = ccxt_gmx_fork_open_close
    captured: dict = {}

    def _stop_after_sltp_construction(sltp_order, *_args, **_kwargs):
        captured["decrease_position_swap_type"] = sltp_order.decrease_position_swap_type
        captured["should_unwrap_native_token"] = sltp_order.should_unwrap_native_token
        raise _Intercepted(captured)

    monkeypatch.setattr(SLTPOrder, "create_increase_order_with_sltp", _stop_after_sltp_construction)

    with pytest.raises(_Intercepted):
        gmx.create_order(
            "ETH/USDC:USDC",
            "market",
            "buy",
            0,
            params={
                "size_usd": 10.0,
                "leverage": 2.0,
                "collateral_symbol": "USDC",
                "takeProfit": {"triggerPercent": 0.10, "closePercent": 1.0},
            },
        )

    assert captured == {
        "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"],
        "should_unwrap_native_token": False,
    }


def test_async_bundled_sltp_forwards_configured_pnl_payout_options(monkeypatch):
    """The async adapter's independent bundled-SL/TP assembly must forward the same options.

    ``eth_defi/gmx/ccxt/async_support/exchange.py::_create_order_with_sltp()``
    builds its own ``SLTPOrder`` directly, exactly like the sync
    ``_create_order_with_sltp()`` above, but is a fully separate code path
    (no shared implementation) -- so the sync fix does not cover it.

    Uses a real, non-mocked ``AsyncGMX`` connected to Arbitrum over
    ``JSON_RPC_ARBITRUM`` -- the same construction as the ``async_gmx_arbitrum``
    fixture in ``test_position_metrics.py``. ``_ensure_session()`` makes one
    real (cheap, read-only) RPC round trip to populate ``chain``/``config``,
    which ``SLTPOrder.__init__`` needs for its own ``web3.eth.chain_id``
    read. The remaining network-bound calls this method makes on its way to
    constructing ``SLTPOrder`` -- ERC-20 metadata, GMX's live oracle-price
    API, and token approval (which would need a funded wallet and broadcast
    a transaction) -- are replaced via ``monkeypatch`` on the real
    functions/methods, not a mock object, so nothing about the assembly
    logic actually under test is faked.
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    gmx = async_exchange.GMX({"rpcUrl": rpc_url})
    captured: dict = {}

    async def _fake_ensure_token_approval_async(*_args, **_kwargs):
        await asyncio.sleep(0)

    class _FakeTokenDetails:
        symbol = "WETH"
        decimals = 18

    def _fake_get_recent_prices(_self, weth_address: HexAddress):
        raw_price = 3_000 * 10 ** (PRECISION - 18)
        return {weth_address: {"maxPriceFull": str(raw_price), "minPriceFull": str(raw_price)}}

    def _stop_after_sltp_construction(sltp_order, *_args, **_kwargs):
        captured["decrease_position_swap_type"] = sltp_order.decrease_position_swap_type
        captured["should_unwrap_native_token"] = sltp_order.should_unwrap_native_token
        raise _Intercepted(captured)

    async def _run() -> None:
        try:
            await gmx._ensure_session()

            weth_address: HexAddress = to_checksum_address(_native_wrapped_token_address(gmx.chain))
            market_address: HexAddress = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")  # GM ETH/USDC

            gmx.markets["ETH/USDC:USDC"] = {
                "base": "ETH",
                "info": {
                    "market_token": market_address,
                    "long_token": weth_address,
                    "index_token": weth_address,
                },
            }

            monkeypatch.setattr(gmx, "_ensure_token_approval_async", _fake_ensure_token_approval_async)
            monkeypatch.setattr(async_exchange, "fetch_erc20_details", lambda *_args, **_kwargs: _FakeTokenDetails())
            monkeypatch.setattr(OraclePrices, "get_recent_prices", lambda self: _fake_get_recent_prices(self, weth_address))
            monkeypatch.setattr(SLTPOrder, "create_increase_order_with_sltp", _stop_after_sltp_construction)

            tp_entry = SLTPEntry(trigger_percent=0.10, close_percent=1.0)

            await gmx._create_order_with_sltp(
                symbol="ETH/USDC:USDC",
                type="market",
                side="buy",
                amount=0,
                price=None,
                params={
                    "size_usd": 10.0,
                    "leverage": 2.0,
                    "collateral_symbol": "USDC",
                    "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
                    "should_unwrap_native_token": True,
                },
                sl_entry=None,
                tp_entry=tp_entry,
            )
        finally:
            await gmx.close()

    with pytest.raises(_Intercepted):
        asyncio.run(_run())

    assert captured == {
        "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
        "should_unwrap_native_token": True,
    }
