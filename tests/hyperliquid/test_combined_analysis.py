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
    - Share price metrics are calculated correctly
    """
    # Check expected columns
    expected_columns = [
        "pnl_update",
        "netflow_update",
        "cumulative_pnl",
        "cumulative_netflow",
        "cumulative_account_value",
        "total_assets",
        "total_supply",
        "share_price",
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

    # Verify total_assets equals cumulative_account_value
    pd.testing.assert_series_equal(
        combined_df["total_assets"],
        combined_df["cumulative_account_value"],
        check_names=False,
    )

    # Share price should be positive where there are shares
    shares_exist = combined_df["total_supply"] > 0
    assert (combined_df.loc[shares_exist, "share_price"] > 0).all(), "Share price should be positive when shares exist"

    # Verify share_price = total_assets / total_supply where shares exist
    if shares_exist.any():
        expected_share_price = combined_df.loc[shares_exist, "total_assets"] / combined_df.loc[shares_exist, "total_supply"]
        pd.testing.assert_series_equal(
            combined_df.loc[shares_exist, "share_price"],
            expected_share_price,
            check_names=False,
        )


def test_combined_summary(combined_df: pd.DataFrame):
    """Test summary generation with expected values."""
    summary = get_combined_summary(combined_df)

    assert summary["total_events"] == len(combined_df)
    assert summary["total_netflow"] == pytest.approx(3650.0, rel=0.01)
    assert summary["final_account_value"] == pytest.approx(summary["total_pnl"] + summary["total_netflow"])

    # Share price metrics should be present
    assert "final_share_price" in summary
    assert "final_total_supply" in summary
    assert "share_price_change" in summary

    # Share price should be positive
    assert summary["final_share_price"] > 0

    # Total supply should equal net deposits at share price 1.0 if no PnL,
    # or be adjusted based on share price changes
    assert summary["final_total_supply"] > 0


def test_share_price_calculation():
    """Test share price calculation with a simple example.

    Verifies the share price mechanism:
    - Initial deposit creates shares at price 1.00
    - PnL changes affect share price
    - Subsequent deposits mint shares at current share price
    """
    # Create simple test data
    # Scenario: 1000 USDC deposit, then 100 USDC profit, then 500 USDC deposit
    import datetime

    timestamps = [
        datetime.datetime(2024, 1, 1, 10, 0, 0),  # First deposit
        datetime.datetime(2024, 1, 1, 11, 0, 0),  # PnL event
        datetime.datetime(2024, 1, 1, 12, 0, 0),  # Second deposit
    ]

    # Position DataFrame with PnL
    position_df = pd.DataFrame(
        {"btc_long_pnl": [0.0, 100.0, 100.0]},
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )

    # Deposit DataFrame
    deposit_df = pd.DataFrame(
        {
            "event_type": ["vault_deposit", "vault_deposit"],
            "usdc": [1000.0, 500.0],
        },
        index=pd.DatetimeIndex([timestamps[0], timestamps[2]], name="timestamp"),
    )

    combined = analyse_positions_and_deposits(position_df, deposit_df)

    # First row: 1000 deposit at share price 1.0 = 1000 shares
    assert combined.iloc[0]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[0]["share_price"] == pytest.approx(1.0)
    assert combined.iloc[0]["total_assets"] == pytest.approx(1000.0)

    # Second row: 100 profit, no new shares, share price increases
    # total_assets = 1100, total_supply = 1000, share_price = 1.1
    assert combined.iloc[1]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[1]["share_price"] == pytest.approx(1.1)
    assert combined.iloc[1]["total_assets"] == pytest.approx(1100.0)

    # Third row: 500 deposit at share price 1.1 = 500/1.1 = 454.545 new shares
    # total_supply = 1000 + 454.545 = 1454.545
    # total_assets = 1100 + 500 = 1600
    # share_price = 1600 / 1454.545 = 1.1 (unchanged because deposit is at current price)
    expected_new_shares = 500.0 / 1.1
    expected_total_supply = 1000.0 + expected_new_shares
    assert combined.iloc[2]["total_supply"] == pytest.approx(expected_total_supply)
    assert combined.iloc[2]["share_price"] == pytest.approx(1.1)
    assert combined.iloc[2]["total_assets"] == pytest.approx(1600.0)


def test_share_price_with_withdrawal():
    """Test share price calculation with withdrawals.

    Verifies:
    - Withdrawals burn shares at current share price
    - Share price remains unchanged after withdrawal
    """
    import datetime

    timestamps = [
        datetime.datetime(2024, 1, 1, 10, 0, 0),  # First deposit
        datetime.datetime(2024, 1, 1, 11, 0, 0),  # PnL event (profit)
        datetime.datetime(2024, 1, 1, 12, 0, 0),  # Withdrawal
    ]

    # Position DataFrame with PnL
    position_df = pd.DataFrame(
        {"btc_long_pnl": [0.0, 200.0, 200.0]},
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )

    # Deposit DataFrame: 1000 deposit, then 500 withdrawal
    deposit_df = pd.DataFrame(
        {
            "event_type": ["vault_deposit", "vault_withdraw"],
            "usdc": [1000.0, -500.0],  # Negative for withdrawal
        },
        index=pd.DatetimeIndex([timestamps[0], timestamps[2]], name="timestamp"),
    )

    combined = analyse_positions_and_deposits(position_df, deposit_df)

    # Row 0: 1000 deposit at price 1.0 = 1000 shares
    assert combined.iloc[0]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[0]["share_price"] == pytest.approx(1.0)

    # Row 1: 200 profit, share price = 1200/1000 = 1.2
    assert combined.iloc[1]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[1]["share_price"] == pytest.approx(1.2)

    # Row 2: 500 withdrawal at price 1.2 = 500/1.2 = 416.67 shares burned
    shares_burned = 500.0 / 1.2
    expected_total_supply = 1000.0 - shares_burned
    assert combined.iloc[2]["total_assets"] == pytest.approx(700.0)
    assert combined.iloc[2]["total_supply"] == pytest.approx(expected_total_supply)
    assert combined.iloc[2]["share_price"] == pytest.approx(1.2)


def test_share_price_with_loss():
    """Test share price calculation when there are trading losses.

    Verifies:
    - Losses decrease share price
    - Deposits after losses mint more shares per dollar
    """
    import datetime

    timestamps = [
        datetime.datetime(2024, 1, 1, 10, 0, 0),  # First deposit
        datetime.datetime(2024, 1, 1, 11, 0, 0),  # PnL event (loss)
        datetime.datetime(2024, 1, 1, 12, 0, 0),  # Second deposit
    ]

    # Position DataFrame with loss
    position_df = pd.DataFrame(
        {"btc_long_pnl": [0.0, -100.0, -100.0]},
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )

    # Deposit DataFrame
    deposit_df = pd.DataFrame(
        {
            "event_type": ["vault_deposit", "vault_deposit"],
            "usdc": [1000.0, 450.0],
        },
        index=pd.DatetimeIndex([timestamps[0], timestamps[2]], name="timestamp"),
    )

    combined = analyse_positions_and_deposits(position_df, deposit_df)

    # Row 0: 1000 deposit at price 1.0 = 1000 shares
    assert combined.iloc[0]["share_price"] == pytest.approx(1.0)
    assert combined.iloc[0]["total_supply"] == pytest.approx(1000.0)

    # Row 1: 100 loss, share price = 900/1000 = 0.9
    assert combined.iloc[1]["total_assets"] == pytest.approx(900.0)
    assert combined.iloc[1]["share_price"] == pytest.approx(0.9)
    assert combined.iloc[1]["total_supply"] == pytest.approx(1000.0)

    # Row 2: 450 deposit at price 0.9 = 450/0.9 = 500 new shares
    # total_supply = 1000 + 500 = 1500
    # total_assets = 900 + 450 = 1350
    # share_price = 1350 / 1500 = 0.9
    assert combined.iloc[2]["total_assets"] == pytest.approx(1350.0)
    assert combined.iloc[2]["total_supply"] == pytest.approx(1500.0)
    assert combined.iloc[2]["share_price"] == pytest.approx(0.9)
