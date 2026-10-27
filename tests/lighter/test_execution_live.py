"""Minimal real public-provider checks for execution-oriented API decoding.

These checks never sign or submit transactions. The funded authenticated
lifecycle is the separate manual round-trip command documented in the tutorial.
"""

from decimal import Decimal

import pytest

from eth_defi.lighter.execution import fetch_execution_account, fetch_pool_state
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.valuation import fetch_lighter_account_by_index
from eth_defi.lighter.vault import fetch_system_config


def test_current_pool_state_uses_live_equity(lighter_session: LighterSession) -> None:
    """Decode current raw share supply and LLP constraints from real responses."""
    config = fetch_system_config(lighter_session)
    state = fetch_pool_state(lighter_session, int(config["liquidity_pool_index"]))
    assert state.is_llp
    assert state.equity > 0 and state.total_shares > 0
    assert state.share_value * state.total_shares == pytest.approx(state.equity)
    assert state.cooldown.total_seconds() >= 0


def test_strict_account_fields_from_real_provider(lighter_session: LighterSession) -> None:
    """The public funding-rebate account exposes the strict execution schema."""
    config = fetch_system_config(lighter_session)
    account, equity = fetch_execution_account(lighter_session, int(config["funding_fee_rebate_account_index"]))
    assert isinstance(account["shares"], list)
    assert equity.collateral.is_finite() and isinstance(equity.available_balance, Decimal)


def test_non_llp_pool_rate_units_from_real_provider(lighter_session: LighterSession) -> None:
    """Check both percentage conversions against a live non-zero-fee pool.

    Candle Effect v2 has non-zero rates, unlike LLP. Compare with the raw API
    record so this check does not freeze an operator's configurable fee.
    See https://app.lighter.xyz/public-pools/281474976543116.

    :param lighter_session: Real Ethereum Lighter public-provider session.
    """
    pool_index = 281474976543116
    raw = fetch_lighter_account_by_index(lighter_session, pool_index)["pool_info"]
    state = fetch_pool_state(lighter_session, pool_index)
    assert not state.is_llp
    if not Decimal(raw["operator_fee"]) or not Decimal(raw["min_operator_share_rate"]):
        pytest.skip("Pool no longer has two non-zero rates to check")
    assert Decimal(raw["operator_fee"]) > 1, "Observed rate no longer distinguishes percentage points; review the pool policy and API units"
    assert Decimal(raw["min_operator_share_rate"]) > 1, "Observed rate no longer distinguishes percentage points; review the pool policy and API units"
    assert state.operator_fee == Decimal(raw["operator_fee"]) / Decimal(100)
    assert state.min_operator_share_rate == Decimal(raw["min_operator_share_rate"]) / Decimal(100)
