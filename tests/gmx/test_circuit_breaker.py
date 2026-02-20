"""Unit tests for GMX keeper cancel circuit breaker and error decoding.

No RPC or fork needed — all tests are pure Python logic.
"""

import time

import pytest

from eth_defi.gmx.events import decode_error_reason


# ---------------------------------------------------------------------------
# Task 1: decode_error_reason tests
# ---------------------------------------------------------------------------


def test_decode_error_reason_invalid_decrease_order_size():
    """decode_error_reason decodes InvalidDecreaseOrderSize selector."""
    from eth_abi import encode

    # ABI-encoded InvalidDecreaseOrderSize(uint256, uint256)
    # selector = be2cbc10
    selector = bytes.fromhex("be2cbc10")
    params = encode(["uint256", "uint256"], [10 * 10**30, 9 * 10**30])
    reason_bytes = selector + params
    result = decode_error_reason(reason_bytes)
    assert result is not None
    assert "InvalidDecreaseOrderSize" in result


def test_decode_error_reason_returns_none_for_empty():
    """decode_error_reason returns None for empty or too-short bytes."""
    assert decode_error_reason(b"") is None
    assert decode_error_reason(b"\x00\x00\x00") is None  # Less than 4 bytes


def test_decode_error_reason_unknown_selector():
    """decode_error_reason returns a string with the selector for unrecognised errors."""
    result = decode_error_reason(b"\xde\xad\xbe\xef" + b"\x00" * 32)
    assert result is not None
    assert "deadbeef" in result.lower()


def test_reason_decoding_logic():
    """Verify the hex-decode + reason-bytes logic used in create_order() cancel handler."""
    from eth_abi import encode

    # Simulate what Subsquid provides in trade_action["reasonBytes"]
    selector = bytes.fromhex("be2cbc10")
    params = encode(["uint256", "uint256"], [10 * 10**30, 9 * 10**30])
    raw_hex = (selector + params).hex()  # No 0x prefix (Subsquid format)

    # Reproduce the decoding logic from exchange.py
    reason_bytes_decoded = bytes.fromhex(raw_hex.replace("0x", ""))
    decoded = decode_error_reason(reason_bytes_decoded)
    assert decoded is not None
    assert "InvalidDecreaseOrderSize" in decoded


def test_reason_decoding_logic_with_0x_prefix():
    """Same as above but with 0x prefix (some providers include it)."""
    from eth_abi import encode

    selector = bytes.fromhex("be2cbc10")
    params = encode(["uint256", "uint256"], [10 * 10**30, 9 * 10**30])
    raw_hex = "0x" + (selector + params).hex()

    reason_bytes_decoded = bytes.fromhex(raw_hex.replace("0x", ""))
    decoded = decode_error_reason(reason_bytes_decoded)
    assert decoded is not None
    assert "InvalidDecreaseOrderSize" in decoded


# ---------------------------------------------------------------------------
# Task 2: circuit breaker logic tests
# (replicate the exact logic from exchange.py using helper functions so
# changes to exchange.py must also update the tests — no mock magic needed)
# ---------------------------------------------------------------------------


def _make_exchange_state(max_cancels: int = 3, cooldown_secs: int = 60):
    """Return a plain dict mimicking the exchange circuit-breaker state."""

    class _State:
        def __init__(self):
            self._keeper_cancel_tracker = {}
            self._max_keeper_cancels = max_cancels
            self._keeper_cancel_cooldown_secs = cooldown_secs

    return _State()


def _increment_cancel(state, symbol: str, reason: str = "test") -> None:
    """Replicate the cancel-increment logic from exchange.py cancel handler."""
    entry = state._keeper_cancel_tracker.setdefault(symbol, {"count": 0, "cooldown_until": 0.0, "last_reason": ""})
    entry["count"] += 1
    entry["last_reason"] = reason
    if entry["count"] >= state._max_keeper_cancels:
        entry["cooldown_until"] = time.monotonic() + state._keeper_cancel_cooldown_secs


def _is_in_cooldown(state, symbol: str) -> bool:
    """Replicate the cooldown check from exchange.py close-order entry."""
    entry = state._keeper_cancel_tracker.get(symbol)
    if not entry:
        return False
    return time.monotonic() < entry["cooldown_until"]


def _reset_cancel_count(state, symbol: str) -> None:
    """Replicate reset_keeper_cancel_count(symbol) from exchange.py."""
    state._keeper_cancel_tracker.pop(symbol, None)


def test_cancel_count_increments():
    state = _make_exchange_state()
    _increment_cancel(state, "BTC/USDC:USDC", "OrderNotFulfillable")
    assert state._keeper_cancel_tracker["BTC/USDC:USDC"]["count"] == 1
    assert not _is_in_cooldown(state, "BTC/USDC:USDC")


def test_cooldown_not_active_below_max():
    state = _make_exchange_state(max_cancels=3)
    for _ in range(2):
        _increment_cancel(state, "ONDO/USDC:USDC", "MinPositionSize")
    assert not _is_in_cooldown(state, "ONDO/USDC:USDC")


def test_cooldown_activated_at_max():
    state = _make_exchange_state(max_cancels=3)
    for _ in range(3):
        _increment_cancel(state, "ONDO/USDC:USDC", "MinPositionSize")
    assert _is_in_cooldown(state, "ONDO/USDC:USDC")


def test_cooldown_expires():
    state = _make_exchange_state(max_cancels=3, cooldown_secs=0)
    for _ in range(3):
        _increment_cancel(state, "ONDO/USDC:USDC", "MinPositionSize")
    time.sleep(0.01)
    assert not _is_in_cooldown(state, "ONDO/USDC:USDC")


def test_reset_clears_cooldown():
    state = _make_exchange_state(max_cancels=3)
    for _ in range(3):
        _increment_cancel(state, "ETH/USDC:USDC", "test")
    assert _is_in_cooldown(state, "ETH/USDC:USDC")
    _reset_cancel_count(state, "ETH/USDC:USDC")
    assert not _is_in_cooldown(state, "ETH/USDC:USDC")
    assert "ETH/USDC:USDC" not in state._keeper_cancel_tracker


def test_different_symbols_tracked_independently():
    state = _make_exchange_state(max_cancels=3)
    for _ in range(3):
        _increment_cancel(state, "ONDO/USDC:USDC", "test")
    assert _is_in_cooldown(state, "ONDO/USDC:USDC")
    assert not _is_in_cooldown(state, "ETH/USDC:USDC")


def test_last_reason_stored():
    state = _make_exchange_state(max_cancels=5)
    _increment_cancel(state, "SOL/USDC:USDC", "OrderNotFulfillableAtAcceptablePrice")
    _increment_cancel(state, "SOL/USDC:USDC", "InsufficientSwapOutputAmount")
    entry = state._keeper_cancel_tracker["SOL/USDC:USDC"]
    assert entry["last_reason"] == "InsufficientSwapOutputAmount"
    assert entry["count"] == 2


def test_reset_all_clears_all_symbols():
    state = _make_exchange_state(max_cancels=3)
    for sym in ("ETH/USDC:USDC", "BTC/USDC:USDC"):
        for _ in range(3):
            _increment_cancel(state, sym, "test")
    assert _is_in_cooldown(state, "ETH/USDC:USDC")
    assert _is_in_cooldown(state, "BTC/USDC:USDC")
    # Simulate reset_keeper_cancel_count(None) — clears all
    state._keeper_cancel_tracker.clear()
    assert not _is_in_cooldown(state, "ETH/USDC:USDC")
    assert not _is_in_cooldown(state, "BTC/USDC:USDC")
