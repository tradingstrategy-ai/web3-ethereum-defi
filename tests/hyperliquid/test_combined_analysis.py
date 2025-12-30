"""Test Hyperliquid combined position and deposit analysis.

This test module verifies the combined_analysis.py module functions for
merging position PnL data with deposit/withdrawal data.

Uses the same test vault as other Hyperliquid tests for consistency.
"""

import pandas as pd
import pytest

from eth_defi.hyperliquid.combined_analysis import analyse_positions_and_deposits, get_combined_summary
from eth_defi.hyperliquid.deposit import create_deposit_dataframe, fetch_vault_deposits
from eth_defi.hyperliquid.position import fetch_vault_fills, reconstruct_position_history
from eth_defi.hyperliquid.position_analysis import create_account_dataframe


@pytest.fixture(scope="module")
def position_df(session, hyperliquid_sample_vault, hyperliquid_test_period_start, hyperliquid_test_period_end) -> pd.DataFrame:
    """Create position analysis DataFrame."""
    fills = list(
        fetch_vault_fills(
            session,
            hyperliquid_sample_vault,
            start_time=hyperliquid_test_period_start,
            end_time=hyperliquid_test_period_end,
        )
    )
    events = list(reconstruct_position_history(fills))
    return create_account_dataframe(events)


@pytest.fixture(scope="module")
def deposit_df(session, hyperliquid_sample_vault, hyperliquid_test_period_start, hyperliquid_test_period_end) -> pd.DataFrame:
    """Create deposit DataFrame."""
    events = list(
        fetch_vault_deposits(
            session,
            hyperliquid_sample_vault,
            start_time=hyperliquid_test_period_start,
            end_time=hyperliquid_test_period_end,
        )
    )
    return create_deposit_dataframe(events)


@pytest.fixture(scope="module")
def combined_df(position_df, deposit_df) -> pd.DataFrame:
    """Create combined analysis DataFrame."""
    return analyse_positions_and_deposits(position_df, deposit_df)


def test_combined_analysis_structure_and_values(combined_df: pd.DataFrame):
    """Test combined DataFrame structure and known values for test period.

    Validates:
    - DataFrame has expected columns
    - Account value formula is correct (pnl + netflow)
    - Known deposit values from test period (3650 USDC)
    """
    # Check expected columns
    expected_columns = [
        "pnl_update",
        "netflow_update",
        "cumulative_pnl",
        "cumulative_netflow",
        "cumulative_account_value",
    ]
    for col in expected_columns:
        assert col in combined_df.columns, f"Expected column {col} in DataFrame"

    # Should have combined events from both positions and deposits
    assert len(combined_df) > 8, "Should have more events than just deposits"

    # Verify account value formula: cumulative_account_value = cumulative_pnl + cumulative_netflow
    expected_value = combined_df["cumulative_pnl"] + combined_df["cumulative_netflow"]
    pd.testing.assert_series_equal(
        combined_df["cumulative_account_value"],
        expected_value,
        check_names=False,
    )

    # Net flow should equal total deposits (3650 USDC) since no withdrawals in test period
    final_netflow = combined_df["cumulative_netflow"].iloc[-1]
    assert final_netflow == pytest.approx(3650.0, rel=0.01), f"Expected ~3650 USDC netflow, got {final_netflow}"


def test_combined_summary(combined_df: pd.DataFrame):
    """Test summary generation with expected values."""
    summary = get_combined_summary(combined_df)

    assert summary["total_events"] == len(combined_df)
    assert summary["total_netflow"] == pytest.approx(3650.0, rel=0.01)
    assert summary["final_account_value"] == pytest.approx(
        summary["total_pnl"] + summary["total_netflow"]
    )
