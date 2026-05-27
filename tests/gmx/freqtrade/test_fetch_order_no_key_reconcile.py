"""Regression tests for no-``order_key`` limit-order reconciliation.

When the CCXT cache has an open limit order without ``info["order_key"]``,
the wrapper cannot ask the normal order-key resolver for status.  The
fallback must query GMX REST ``/v1/orders?address=<wallet>`` through
``GMXAPI.get_orders``:

* matching pending REST row -> adopt the row key and keep ``status="open"``;
* successful REST response with no matching row -> mark cancelled;
* REST failure or ambiguous market context -> leave the original order open.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("freqtrade.enums", reason="freqtrade.enums required for these tests")


DOGE_MARKET = "0x" + "11" * 20
FIL_MARKET = "0x" + "22" * 20
DOGE_INDEX = "0x" + "aa" * 20
FIL_INDEX = "0x" + "bb" * 20


def _fake_gmx(
    *,
    positions: list[dict] | None = None,
    rest_orders: list[dict] | None = None,
    markets: dict | None = None,
    chain: str = "arbitrum",
):
    """Build a ``Gmx`` instance with selective ``_api`` mocks.

    The fixture wires up both the REST tier (``_api.api.get_orders``)
    and the on-chain Reader tier (``_api.web3``, ``_api.config.get_chain``).
    Reader-tier scans go through ``fetch_pending_orders`` which is
    patched per-test via the :func:`_with_reader` context manager.
    """
    from eth_defi.gmx.freqtrade.gmx_exchange import Gmx

    fake = Gmx.__new__(Gmx)
    fake._api = MagicMock()
    fake._api.fetch_positions = MagicMock(return_value=positions or [])
    fake._api.api = MagicMock()
    fake._api.api.get_orders = MagicMock(return_value=rest_orders or [])
    fake._api.wallet_address = "0xE3F16770C0A336103d7c24B34A4AfcBf6fb17583"
    fake._api._patch_cached_order_key = MagicMock()
    fake._api.web3 = MagicMock()
    fake._api.config = MagicMock()
    fake._api.config.get_chain = MagicMock(return_value=chain)
    fake._api.markets = (
        markets
        if markets is not None
        else {
            "DOGE/USDC:USDC": {"info": {"market_token": DOGE_MARKET, "index_token": DOGE_INDEX}},
            "FIL/USDC:USDC": {"info": {"market_token": FIL_MARKET, "index_token": FIL_INDEX}},
        }
    )
    fake._api._token_metadata = {
        DOGE_INDEX: {"symbol": "DOGE", "decimals": 8},
        FIL_INDEX: {"symbol": "FIL", "decimals": 18},
    }
    fake._last_reconcile_ms = {}
    return fake


#: Default 32-byte hex used by Reader-tier mocks.  The wrapper logs a
#: truncated form so an obviously fake value (alternating 0x33) is fine.
_READER_KEY_BYTES = b"\x33" * 32


def _reader_order(*, order_key: bytes = _READER_KEY_BYTES, market: str = DOGE_MARKET, is_long: bool = True, trigger_price_usd: float = 0.10086039):
    """Build a :class:`PendingOrder`-shaped mock for the Reader tier.

    Real ``PendingOrder`` is a ``dataclass(slots=True)`` so we cannot
    set arbitrary attributes on a real instance; the mock object just
    needs ``order_key`` (raw ``bytes``, NOT hex string —
    :meth:`Gmx._reconcile_no_key_via_contract` formats it as
    ``"0x" + order.order_key.hex()``), ``trigger_price_usd`` (float),
    plus the side attributes the matcher inspects.
    """
    mock = MagicMock()
    mock.order_key = order_key
    mock.trigger_price_usd = trigger_price_usd
    mock.market = market
    mock.is_long = is_long
    return mock


def _with_reader(pending_orders=None, raises=None):
    """Patch ``fetch_pending_orders`` for the Reader tier.

    Use as a context manager around the wrapper call::

        with _with_reader(pending_orders=[]):
            resolved = _invoke(gmx, cached)
    """
    if raises is not None:
        return patch(
            "eth_defi.gmx.order.pending_orders.fetch_pending_orders",
            side_effect=raises,
        )
    return patch(
        "eth_defi.gmx.order.pending_orders.fetch_pending_orders",
        return_value=iter(pending_orders or []),
    )


def _cached_open_limit(
    *,
    side: str = "buy",
    amount: float = 5.07005546,
    price: float = 0.10086039,
    order_id: str = "0xcreationtx",
    pair: str = "DOGE/USDC:USDC",
    with_order_key: bool = False,
) -> dict:
    """CCXT-shaped cached limit order, defaulting to the no-key bug case."""
    info: dict = {"tx_hash": order_id}
    if with_order_key:
        info["order_key"] = "0xexisting"
    return {
        "id": order_id,
        "type": "limit",
        "status": "open",
        "side": side,
        "amount": amount,
        "filled": 0.0,
        "remaining": amount,
        "price": price,
        "symbol": pair,
        "timestamp": int(datetime(2026, 5, 20, tzinfo=timezone.utc).timestamp() * 1000),
        "info": info,
    }


def _rest_order(
    *,
    is_long: bool = True,
    price: float = 0.10086039,
    order_key: str = "0xorderkey",
    market: str = DOGE_MARKET,
    token_decimals: int = 8,
) -> dict:
    """GMX REST ``/orders`` row with raw token-decimal-aware trigger price."""
    return {
        "key": order_key,
        "market": market,
        "isLong": is_long,
        "triggerPrice": str(int(price * 10 ** (30 - token_decimals))),
        "sizeDeltaUsd": str(int(100 * 1e30)),
    }


def _invoke(fake_gmx, order: dict, pair: str = "DOGE/USDC:USDC", *, reader_pending=None, reader_raises=None) -> dict:
    """Stub the parent ``Exchange.fetch_order`` and Reader tier, then call the wrapper.

    By default the Reader returns an empty iterator so REST is the
    only voting tier — preserves the original single-tier semantics
    for tests that don't care about the contract path.

    :param fake_gmx: Fixture-built Gmx wrapper.
    :param order: CCXT order dict returned by the parent ``fetch_order``.
    :param pair: Unified ccxt symbol.
    :param reader_pending: List of ``PendingOrder``-like mocks to feed
        the Reader tier (default: empty list = "checked, no match").
    :param reader_raises: Exception class to raise from
        ``fetch_pending_orders`` (default: no exception).
    """
    parent_patch = patch(
        "eth_defi.gmx.freqtrade.gmx_exchange.Exchange.fetch_order",
        return_value=order,
    )
    reader_patch = _with_reader(pending_orders=reader_pending or [], raises=reader_raises)
    with parent_patch, reader_patch:
        return fake_gmx.fetch_order(order["id"], pair)


class TestNoKeyRestOrderResolver:
    """Tier-A: REST ``/v1/orders``.  Reader tier is stubbed empty so
    these tests pin REST-only behaviour against the cascade contract.
    """

    def test_no_key_open_limit_adopts_rest_pending_order_key(self):
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[_rest_order(order_key="0xorderkey")])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "open"
        assert resolved["info"]["order_key"] == "0xorderkey"
        assert resolved["info"]["reconciled_via_rest_orders"] is True
        gmx._api.api.get_orders.assert_called_once_with(gmx._api.wallet_address)
        gmx._api._patch_cached_order_key.assert_called_once_with("0xcreationtx", "0xorderkey")

    def test_no_key_open_limit_matches_fil_raw_trigger_price_with_18_decimals(self):
        cached = _cached_open_limit(
            amount=4.90272068,
            price=0.91901046,
            pair="FIL/USDC:USDC",
        )
        gmx = _fake_gmx(
            rest_orders=[
                _rest_order(
                    price=0.91901046,
                    order_key="0xfilorderkey",
                    market=FIL_MARKET,
                    token_decimals=18,
                )
            ]
        )

        resolved = _invoke(gmx, cached, pair="FIL/USDC:USDC")

        assert resolved["status"] == "open"
        assert resolved["info"]["order_key"] == "0xfilorderkey"

    def test_existing_order_key_skips_cascade_entirely(self):
        cached = _cached_open_limit(with_order_key=True)
        gmx = _fake_gmx(rest_orders=[_rest_order(order_key="0xanotherkey")])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "open"
        gmx._api.api.get_orders.assert_not_called()
        gmx._api._patch_cached_order_key.assert_not_called()

    def test_no_matching_pending_order_anywhere_returns_cancelled(self):
        # REST has rows but no match; Reader empty (default).  Both
        # tiers vote absent → cancelled.
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[_rest_order(market=FIL_MARKET)])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "cancelled"
        assert resolved["filled"] == 0.0
        assert resolved["info"]["gmx_status"] == "no_key_not_found_in_any_tier"
        assert "REST" in resolved["info"]["cancel_reason"] or "gmxapi.ai" in resolved["info"]["cancel_reason"]

    def test_rest_exception_with_empty_reader_keeps_open(self):
        # REST errored → inconclusive.  Reader empty alone is not
        # enough; the cancel vote requires BOTH tiers to confirm.
        cached = _cached_open_limit()
        gmx = _fake_gmx()
        gmx._api.api.get_orders = MagicMock(side_effect=ConnectionError("gmxapi.ai 503"))

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "open"
        assert "gmx_status" not in resolved["info"]

    def test_unknown_market_with_rest_rows_is_inconclusive(self):
        cached = _cached_open_limit()
        gmx = _fake_gmx(markets={}, rest_orders=[_rest_order(order_key="0xwrong")])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "open"
        assert "order_key" not in resolved["info"]
        assert "gmx_status" not in resolved["info"]
        gmx._api._patch_cached_order_key.assert_not_called()

    def test_empty_rest_orders_with_empty_reader_marks_cancelled(self):
        # The original "phantom" case (stuck multistrat trades): both
        # tiers can resolve the market (DOGE in default fixture) and
        # both return no pending row → flip cancelled.  Unknown market
        # would be inconclusive (see test_unknown_market_*).
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "cancelled"
        assert resolved["info"]["gmx_status"] == "no_key_not_found_in_any_tier"

    def test_absent_pending_order_with_same_pair_position_marks_filled_even_when_amount_units_drift(self):
        # Production DOGE/TAO regression: after restart the cached no-key
        # entry amount can degrade to USD notional while the live GMX
        # position reports base-token contracts.  If both pending-order
        # tiers are empty, the same-pair position is stronger evidence of
        # fill than "cancelled"; use the position size as filled amount.
        cached = _cached_open_limit(amount=5.07005546, price=0.10086039)
        gmx = _fake_gmx(
            positions=[
                {
                    "symbol": "DOGE/USDC:USDC",
                    "side": "long",
                    "contracts": 50.27877164,
                    "entryPrice": 0.10083888881985023,
                    "id": "0xdogepos",
                },
            ],
            rest_orders=[],
        )

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "closed"
        assert resolved["filled"] == pytest.approx(50.27877164)
        assert resolved["remaining"] == 0.0
        assert resolved["average"] == pytest.approx(0.10083888881985023)
        assert resolved["info"]["reconciled_via_position"] is True
        assert resolved["info"]["reconciled_position_size"] == pytest.approx(50.27877164)

    def test_multiple_same_side_candidates_match_by_trigger_price(self):
        cached = _cached_open_limit(price=0.10086039)
        gmx = _fake_gmx(
            rest_orders=[
                _rest_order(price=0.22, order_key="0xwrong"),
                _rest_order(price=0.10086039, order_key="0xright"),
            ],
        )

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "open"
        assert resolved["info"]["order_key"] == "0xright"

    def test_single_rest_candidate_with_wrong_trigger_price_does_not_match(self):
        # Even one same-market/same-side REST candidate is not enough
        # if the cached order has a trigger price and the row's trigger
        # price is different.
        cached = _cached_open_limit(price=0.10086039)
        gmx = _fake_gmx(rest_orders=[_rest_order(price=0.22, order_key="0xwrong")])

        resolved = _invoke(gmx, cached)

        assert resolved["status"] == "cancelled"
        assert "order_key" not in resolved["info"]
        gmx._api._patch_cached_order_key.assert_not_called()


class TestNoKeyContractResolver:
    """Tier-B: on-chain Reader (``SyntheticsReader.getAccountOrders``).

    REST tier is set to empty/absent so we can isolate Reader-tier
    behaviour.
    """

    def test_reader_finds_order_after_rest_returns_empty(self):
        # REST list empty, Reader has matching pending order →
        # adopt the Reader key, keep open.
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[])

        resolved = _invoke(
            gmx,
            cached,
            reader_pending=[_reader_order()],
        )

        expected_key = "0x" + _READER_KEY_BYTES.hex()
        assert resolved["status"] == "open"
        assert resolved["info"]["order_key"] == expected_key
        assert resolved["info"]["reconciled_via_reader"] is True
        gmx._api._patch_cached_order_key.assert_called_once_with("0xcreationtx", expected_key)

    def test_reader_overrides_rest_apparent_absence(self):
        # REST shows no pending (success+empty) but Reader has a
        # matching order — keep open (Reader is authoritative).
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[])

        resolved = _invoke(
            gmx,
            cached,
            reader_pending=[_reader_order()],
        )

        assert resolved["status"] == "open"
        assert resolved["info"]["order_key"] == "0x" + _READER_KEY_BYTES.hex()

    def test_reader_exception_with_rest_absence_keeps_open(self):
        # REST returned no match (checked); Reader RPC errored
        # (inconclusive).  Cancellation needs BOTH checked-and-empty;
        # leave the order open.
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[])

        resolved = _invoke(gmx, cached, reader_raises=ConnectionError("rpc 503"))

        assert resolved["status"] == "open"
        assert "gmx_status" not in resolved["info"]

    def test_rest_and_reader_both_error_keeps_open(self):
        cached = _cached_open_limit()
        gmx = _fake_gmx()
        gmx._api.api.get_orders = MagicMock(side_effect=ConnectionError("rest 503"))

        resolved = _invoke(gmx, cached, reader_raises=ConnectionError("rpc 503"))

        assert resolved["status"] == "open"
        assert "gmx_status" not in resolved["info"]

    def test_reader_unknown_market_with_pending_rows_is_inconclusive(self):
        # Without a pair -> market-token mapping, the Reader tier must
        # not scan all same-side wallet orders and adopt the sole row.
        # That could patch DOGE/FIL/TAO with another pair's order_key.
        cached = _cached_open_limit()
        gmx = _fake_gmx(markets={}, rest_orders=[])

        resolved = _invoke(
            gmx,
            cached,
            reader_pending=[_reader_order(market=FIL_MARKET)],
        )

        assert resolved["status"] == "open"
        assert "order_key" not in resolved["info"]
        assert "gmx_status" not in resolved["info"]
        gmx._api._patch_cached_order_key.assert_not_called()

    def test_single_reader_candidate_with_wrong_trigger_price_does_not_match(self):
        # Reader candidate is filtered by market + side, but if cached
        # price exists the trigger price still has to agree before we
        # adopt its order_key.
        cached = _cached_open_limit(price=0.10086039)
        gmx = _fake_gmx(rest_orders=[])

        resolved = _invoke(
            gmx,
            cached,
            reader_pending=[_reader_order(trigger_price_usd=0.22)],
        )

        assert resolved["status"] == "cancelled"
        assert "order_key" not in resolved["info"]
        gmx._api._patch_cached_order_key.assert_not_called()


class TestThrottle:
    def test_second_call_within_throttle_window_skips_rest_lookup(self):
        cached = _cached_open_limit()
        gmx = _fake_gmx(rest_orders=[_rest_order(order_key="0xorderkey")])

        _invoke(gmx, cached)
        gmx._api.api.get_orders.reset_mock()
        _invoke(gmx, cached)

        gmx._api.api.get_orders.assert_not_called()
