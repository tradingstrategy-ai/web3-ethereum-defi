"""Test Hyperliquid position analysis DataFrame creation.

This test module verifies the create_account_dataframe function
that converts position events into a pandas DataFrame.

Uses the same test vault and time range as test_vault_history.py
for consistency.
"""

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from eth_defi.hyperliquid.position import (Fill, PositionDirection,
                                           PositionEvent, PositionEventType,
                                           fetch_vault_fills,
                                           reconstruct_position_history)
from eth_defi.hyperliquid.position_analysis import create_account_dataframe
from eth_defi.hyperliquid.session import create_hyperliquid_session

#: Test vault address (Trading Strategy - IchiV3 LS)
TEST_VAULT_ADDRESS = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

#: Fixed test time range start
TEST_START_TIME = datetime(2025, 12, 1)

#: Fixed test time range end
TEST_END_TIME = datetime(2025, 12, 28)


@pytest.fixture(scope="module")
def session():
    """Create a shared session for all tests in this module."""
    return create_hyperliquid_session()


@pytest.fixture(scope="module")
def vault_fills(session) -> list[Fill]:
    """Fetch fills for the test vault."""
    fills = list(fetch_vault_fills(
        session,
        TEST_VAULT_ADDRESS,
        start_time=TEST_START_TIME,
        end_time=TEST_END_TIME,
    ))
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
