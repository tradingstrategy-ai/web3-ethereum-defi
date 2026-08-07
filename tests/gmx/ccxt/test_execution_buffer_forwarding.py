"""The adapter's configured execution-fee buffer must reach the order builder.

``_convert_ccxt_to_gmx_params()`` does not emit ``execution_buffer``, so without an
explicit hand-off ``GMXTrading`` falls back to its own default and the
``executionBuffer`` constructor parameter is silently discarded — raising it has no
effect on the fee actually supplied, and GMX rejects the order with
``InsufficientExecutionFee``. Closing a position always forwarded the value;
opening did not.

These run against an Anvil fork because ``create_order()`` resolves markets and
oracle prices before it builds order parameters. The forwarding is intercepted at
``GMXTrading.open_position`` so nothing is broadcast.
"""

import pytest

#: Distinctive value, unlike any default in the codebase, so a match cannot be
#: coincidental.
SENTINEL_BUFFER = 17.5


class _Intercepted(Exception):
    """Raised by the stub to stop before anything is signed or broadcast."""

    def __init__(self, kwargs: dict):
        super().__init__("intercepted")
        self.kwargs = kwargs


def _capture_open_position(gmx, monkeypatch) -> dict:
    """Replace the order builder with a stub that records its keyword arguments."""
    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        raise _Intercepted(kwargs)

    monkeypatch.setattr(gmx.trader, "open_position", _stub)
    return captured


def _open(gmx, params: dict) -> None:
    """Attempt a market open, swallowing the interception."""
    with pytest.raises(_Intercepted):
        gmx.create_order(
            symbol="ETH/USDC:USDC",
            type="market",
            side="buy",
            amount=0,
            params={
                "size_usd": 10.0,
                "leverage": 2.0,
                "collateral_symbol": "USDC",
                "wait_for_execution": False,
                **params,
            },
        )


def test_instance_execution_buffer_is_forwarded_on_open(ccxt_gmx_fork_open_close, monkeypatch):
    """The configured buffer must reach ``open_position`` when the caller sets none.

    This is the regression: the value was accepted at construction, stored, and
    then dropped, so orders were priced with GMXTrading's default instead.
    """
    gmx = ccxt_gmx_fork_open_close
    gmx.execution_buffer = SENTINEL_BUFFER
    captured = _capture_open_position(gmx, monkeypatch)

    _open(gmx, {})

    assert captured.get("execution_buffer") == SENTINEL_BUFFER


def test_explicit_execution_buffer_wins_over_the_instance_default(ccxt_gmx_fork_open_close, monkeypatch):
    """A per-order buffer must not be clobbered by the instance default.

    The forwarding uses ``setdefault`` rather than an unconditional assignment
    precisely so this override survives.
    """
    gmx = ccxt_gmx_fork_open_close
    gmx.execution_buffer = SENTINEL_BUFFER
    captured = _capture_open_position(gmx, monkeypatch)

    _open(gmx, {"execution_buffer": 3.5})

    assert captured.get("execution_buffer") == 3.5
