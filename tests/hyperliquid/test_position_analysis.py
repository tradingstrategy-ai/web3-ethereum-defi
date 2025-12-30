"""Test Hyperliquid position analysis DataFrame creation.

This test module verifies the create_account_dataframe function
that converts position events into a pandas DataFrame.

Uses the same test vault and time range as test_vault_history.py
for consistency.
"""

import pandas as pd
import pytest

from eth_defi.hyperliquid.position import Fill, PositionEvent, fetch_vault_fills, reconstruct_position_history
from eth_defi.hyperliquid.position_analysis import create_account_dataframe


@pytest.fixture(scope="module")
def vault_fills(session, hyperliquid_sample_vault, hyperliquid_test_period_start, hyperliquid_test_period_end) -> list[Fill]:
    """Fetch fills for the test vault."""
    fills = list(
        fetch_vault_fills(
            session,
            hyperliquid_sample_vault,
            start_time=hyperliquid_test_period_start,
            end_time=hyperliquid_test_period_end,
        )
    )
    return fills


@pytest.fixture(scope="module")
def position_events(vault_fills) -> list[PositionEvent]:
    """Reconstruct position events from fills."""
    return list(reconstruct_position_history(vault_fills))


@pytest.fixture(scope="module")
def account_df(position_events) -> pd.DataFrame:
    """Create account DataFrame from position events."""
    return create_account_dataframe(position_events)


def test_total_pnl_can_be_calculated(account_df: pd.DataFrame):
    """Test that total account PnL can be calculated by summing pnl columns."""
    pnl_columns = [col for col in account_df.columns if col.endswith("_pnl")]
    total_pnl = account_df[pnl_columns].sum(axis=1)

    # Last row should have total realized PnL
    assert isinstance(total_pnl.iloc[-1], float)
    # There should be some realized PnL in the test period
    assert total_pnl.iloc[-1] != 0, "Expected some realized PnL"


def test_final_pnl_values_match_summary(
    account_df: pd.DataFrame,
    position_events: list[PositionEvent],
):
    """Test that final PnL values match expected values from summary."""
    # Get final row
    final_row = account_df.iloc[-1]

    # AAVE should have specific realized PnL
    aave_total_pnl = final_row.get("AAVE_long_pnl", 0) + final_row.get("AAVE_short_pnl", 0)
    assert abs(aave_total_pnl - 96.6087) < 0.01, f"AAVE PnL mismatch: {aave_total_pnl}"
