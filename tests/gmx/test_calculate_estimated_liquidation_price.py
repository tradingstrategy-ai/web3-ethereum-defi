"""Regression tests for :func:`calculate_estimated_liquidation_price`.

The approximate formula used to return ``0.0`` as a sentinel when its inputs
could not produce a meaningful liquidation price (small positions where the
``GMX_MIN_LIQUIDATION_COLLATERAL_USD`` floor dominated the maximum loss).

That sentinel was downstream-misinterpreted: Freqtrade's
``stoploss_or_liquidation()`` only treats ``None`` as "unset", not ``0.0``.
Worse, ``freqtrade.exchange.exchange.get_liquidation_price()`` applies a 5 %
buffer to the value it receives — so a ``0.0`` sentinel became
``0 + 0.05 × open_rate`` in the trade row, which then reproducibly fired
``ExitType.LIQUIDATION`` every loop and crashed the bot 37 times in a row.

The fix:

- Function return type is now ``float | None`` (was ``float``).
- All previously-``0.0`` early-returns now return ``None``.
- New ``_LIQUIDATION_APPROX_MIN_BUFFER_RATIO`` guard rejects approximations
  dominated by the min-collateral floor (max_loss / size_usd below the
  threshold).
- Direction invariant enforced at the bottom: a long's liquidation must be
  below entry, a short's must be above.  Anything else returns ``None``.

These tests pin the new contract so the regression cannot reappear.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.constants import GMX_MIN_LIQUIDATION_COLLATERAL_USD
from eth_defi.gmx.utils import calculate_estimated_liquidation_price


class TestSmallStakeFloorDominated:
    """Pre-fix bug: stake≈floor produced near-entry liquidation values."""

    def test_1x_long_5usd_stake_returns_none(self):
        """The production trigger: ICP/USDC 1× long at $2.55 with $5.05 stake.

        Pre-fix this returned ~$2.527 (0.89 % below entry).  Freqtrade then
        added a 5 % buffer → ~$2.65 which exceeded current rate every loop,
        firing false ``ExitType.LIQUIDATION`` and crashing on the
        ``skip_custom_exit_price`` kwarg.  Post-fix: ``None``.
        """
        result = calculate_estimated_liquidation_price(
            entry_price=2.5522,
            collateral_usd=5.05,
            size_usd=5.05,
            is_long=True,
        )
        assert result is None

    def test_1x_short_5usd_stake_returns_none(self):
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=5.05,
            size_usd=5.05,
            is_long=False,
        )
        assert result is None

    @pytest.mark.parametrize("size_usd", [4.0, 4.5, 5.0, 5.05])
    def test_size_at_or_below_floor_returns_none(self, size_usd):
        """At sizes ≤ the $5 floor, ``max_loss <= 0`` so the early-return
        fires and the formula refuses to guess."""
        result = calculate_estimated_liquidation_price(
            entry_price=10.0,
            collateral_usd=size_usd,
            size_usd=size_usd,
            is_long=True,
        )
        assert result is None

    @pytest.mark.parametrize("size_usd", [6.0, 8.0])
    def test_size_above_floor_yields_value(self, size_usd):
        """At sizes meaningfully above the floor the approximation IS valid
        — the ratio guard does not fire because ``max_loss / size_usd`` >> 1%.

        For ``size = $6`` with ``$5`` floor: ``max_loss = $0.994``,
        ``ratio = 16.5%`` which is well above the 1% threshold.
        """
        result = calculate_estimated_liquidation_price(
            entry_price=10.0,
            collateral_usd=size_usd,
            size_usd=size_usd,
            is_long=True,
        )
        assert result is not None
        assert 0 < result < 10.0


class TestSentinelPathsReturnNone:
    """Previously returned ``0.0`` — now must return ``None``."""

    def test_negative_max_loss_returns_none(self):
        # max_loss = remaining (≈0) - min_floor (5) = negative
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=0.1,
            size_usd=0.1,
            is_long=True,
        )
        assert result is None

    def test_same_token_denom_zero_returns_none(self):
        # size_in_tokens + collateral_amount == 0 (degenerate)
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=10.0,
            size_usd=0.0,  # size_in_tokens = 0 / entry = 0
            is_long=True,
            collateral_is_index_token=True,
            collateral_amount=0.0,
        )
        assert result is None

    def test_size_in_tokens_zero_different_token_returns_none(self):
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=10.0,
            size_usd=0.0,
            is_long=True,
            collateral_is_index_token=False,
            collateral_amount=10.0,
        )
        assert result is None


class TestDirectionInvariant:
    """A long's liquidation must be < entry; short's must be > entry."""

    def test_long_liquidation_strictly_below_entry(self):
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=200.0,  # large enough that approximation is meaningful
            size_usd=1000.0,       # 5× leverage
            is_long=True,
        )
        assert result is not None
        assert result < 100.0
        assert result > 0.0

    def test_short_liquidation_strictly_above_entry(self):
        result = calculate_estimated_liquidation_price(
            entry_price=100.0,
            collateral_usd=200.0,
            size_usd=1000.0,
            is_long=False,
        )
        assert result is not None
        assert result > 100.0


class TestRealisticLeveragedPositions:
    """Confirm the approximation still works for genuinely-meaningful inputs."""

    def test_5x_long_eth_returns_sensible_liquidation(self):
        """ETH long at $2000, 5× leverage with $1000 collateral.

        Approx liquidation should be roughly 20 % below entry (~$1600).
        """
        result = calculate_estimated_liquidation_price(
            entry_price=2000.0,
            collateral_usd=1000.0,
            size_usd=5000.0,
            is_long=True,
            pending_funding_fees_usd=0.0,
            pending_borrowing_fees_usd=0.0,
        )
        assert result is not None
        assert 1400.0 < result < 1900.0  # generous bounds

    def test_10x_long_btc_returns_sensible_liquidation(self):
        result = calculate_estimated_liquidation_price(
            entry_price=70000.0,
            collateral_usd=1000.0,
            size_usd=10000.0,
            is_long=True,
        )
        assert result is not None
        assert 60000.0 < result < 70000.0


class TestExactMode:
    """Exact (collateral_amount provided) path still works."""

    def test_exact_long_returns_value(self):
        result = calculate_estimated_liquidation_price(
            entry_price=2000.0,
            collateral_usd=1000.0,
            collateral_amount=0.5,
            size_usd=5000.0,
            is_long=True,
            collateral_is_index_token=True,
        )
        assert result is not None
        assert 0 < result < 2000.0

    def test_exact_denominator_zero_returns_none(self):
        # size_in_tokens (= size_usd / entry) cancels collateral exactly
        # for short with same-token collateral, denominator = 0
        result = calculate_estimated_liquidation_price(
            entry_price=2000.0,
            collateral_usd=1000.0,
            collateral_amount=2.5,  # exactly size_in_tokens (5000/2000)
            size_usd=5000.0,
            is_long=False,
            collateral_is_index_token=True,
        )
        assert result is None


class TestProductionDataReplay:
    """Replay the exact inputs that crashed the bot on 2026-05-24/25."""

    @pytest.mark.parametrize(
        ("pair", "entry", "stake"),
        [
            ("ICP", 2.5522, 5.05),     # trade 1 — crash trigger
            ("INJ", 5.2174, 4.88),     # trade 2 — filled, 5% buffer artefact
            ("PENDLE", 1.8508, 4.14),  # trade 7 — filled, 5% buffer artefact
            ("SUI", 1.0426, 4.00),     # trade 8 — filled, 5% buffer artefact
        ],
    )
    def test_filled_trades_yield_no_bogus_liquidation_price(self, pair, entry, stake):
        """Every fill on 2026-05-24 produced a corrupt liquidation_price.

        Post-fix: small-stake 1× longs uniformly return ``None`` so Freqtrade
        records ``liquidation_price = NULL`` and the false-LIQUIDATION gate
        never fires.
        """
        result = calculate_estimated_liquidation_price(
            entry_price=entry,
            collateral_usd=stake,
            size_usd=stake,
            is_long=True,
        )
        assert result is None, f"{pair}: expected None, got {result}"
