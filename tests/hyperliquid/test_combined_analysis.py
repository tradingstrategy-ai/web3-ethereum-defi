"""Test Hyperliquid combined position and deposit analysis.

This test module verifies the combined_analysis.py module functions for
merging position PnL data with deposit/withdrawal data.

Uses the same test vault as other Hyperliquid tests for consistency.
"""

import pandas as pd
import pytest

from eth_defi.hyperliquid.combined_analysis import (
    SHARE_PRICE_RESET_THRESHOLD,
    _calculate_share_price,
    analyse_positions_and_deposits,
    get_combined_summary,
)
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
        "epoch_reset",
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


def test_share_price_total_supply_wipeout():
    """Test chain-linked share price epoch reset when total_supply drops to zero.

    Models the "pmalt" vault (0x4dec0a851849056e259128464ef28ce78afa27f6) bug
    where all followers withdraw, total_supply goes to zero, but leader equity
    persists (total_assets > 0). The epoch reset carries forward the last
    share price (chain-linked) instead of resetting to 1.0, keeping the
    price series continuous.

    Scenario:

    - T0: Initial deposit of $1000
    - T1: PnL profit of $100 (AV=$1100, SP rises to 1.1)
    - T2: Full withdrawal of $1100 (total_supply → 0), leader equity of $50 remains
    - T3: Leader trades alone, PnL +$10 (AV=$60, no shares)
    - T4: New deposit of $5000 (AV=$5060)
    - T5: PnL profit of $200 (AV=$5260)

    Verifies:

    - Share price carries forward after wipeout (chain-linked epoch reset)
    - epoch_reset column is True at the reset row
    - New deposits mint shares at the carried-forward price
    - PnL after re-entry correctly moves share price
    - Share price never hits the 10,000 cap
    """
    import datetime

    timestamps = pd.DatetimeIndex(
        [
            datetime.datetime(2024, 1, 1),  # T0: deposit $1000
            datetime.datetime(2024, 1, 2),  # T1: PnL +$100
            datetime.datetime(2024, 1, 3),  # T2: withdraw $1100 (full exit)
            datetime.datetime(2024, 1, 4),  # T3: leader PnL +$10
            datetime.datetime(2024, 1, 5),  # T4: new deposit $5000
            datetime.datetime(2024, 1, 6),  # T5: PnL +$200
        ],
        name="timestamp",
    )

    # Build the DataFrame that _calculate_share_price expects:
    # cumulative_account_value (= total_assets) and netflow_update
    #
    # T0: netflow=+1000, pnl=0,   AV=1000
    # T1: netflow=0,     pnl=+100, AV=1100
    # T2: netflow=-1100, pnl=0,   AV=0 → but leader has $50 equity
    #     (the derived netflow is -1100, but the API AV = 50 because
    #      AV includes leader equity that isn't captured by the share model)
    #     So: AV_change = 50 - 1100 = -1050, pnl_update = -50,
    #     netflow = -1050 - (-50) = -1000. But we want total_supply → 0.
    #     For total_supply to reach 0: need netflow = -total_supply * SP
    #     At T1: total_supply=1000, SP=1.1, so burn = 1100/1.1 = 1000 shares
    #     That means netflow must be -1100 to burn all 1000 shares.
    # T3: netflow=0,     pnl=+10, AV=60
    # T4: netflow=+5000, pnl=0,   AV=5060
    # T5: netflow=0,     pnl=+200, AV=5260

    combined = pd.DataFrame(
        {
            "netflow_update": [1000.0, 0.0, -1100.0, 0.0, 5000.0, 0.0],
            "cumulative_account_value": [1000.0, 1100.0, 50.0, 60.0, 5060.0, 5260.0],
            "pnl_update": [0.0, 100.0, -50.0, 10.0, 0.0, 200.0],
            "cumulative_pnl": [0.0, 100.0, 50.0, 60.0, 60.0, 260.0],
        },
        index=timestamps,
    )

    combined = _calculate_share_price(combined, initial_balance=0.0)

    # T0: 1000 deposit at SP=1.0 → 1000 shares
    assert combined.iloc[0]["share_price"] == pytest.approx(1.0)
    assert combined.iloc[0]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[0]["epoch_reset"] == False

    # T1: +100 PnL, AV=1100, supply=1000, SP=1.1
    assert combined.iloc[1]["share_price"] == pytest.approx(1.1)
    assert combined.iloc[1]["total_supply"] == pytest.approx(1000.0)
    assert combined.iloc[1]["epoch_reset"] == False

    # T2: Withdraw 1100 at SP=1.1 → burn 1100/1.1 = 1000 shares → supply=0
    # AV=50 (leader equity), total_supply=0 → chain-linked epoch reset:
    # epoch_anchor=1.1, total_supply=50/1.1≈45.45, SP=1.1 (carried forward)
    epoch_anchor = 1.1
    expected_supply_after_reset = 50.0 / epoch_anchor
    assert combined.iloc[2]["total_assets"] == pytest.approx(50.0)
    assert combined.iloc[2]["share_price"] == pytest.approx(epoch_anchor)
    assert combined.iloc[2]["total_supply"] == pytest.approx(expected_supply_after_reset)
    assert combined.iloc[2]["epoch_reset"] == True

    # T3: Leader PnL +10, AV=60, supply≈45.45, SP=60/45.45≈1.32
    expected_sp_t3 = 60.0 / expected_supply_after_reset
    assert combined.iloc[3]["share_price"] == pytest.approx(expected_sp_t3)
    assert combined.iloc[3]["total_supply"] == pytest.approx(expected_supply_after_reset)
    assert combined.iloc[3]["epoch_reset"] == False

    # T4: New deposit $5000 at SP≈1.32 → mint 5000/1.32≈3787.88 shares
    # total_supply≈45.45+3787.88≈3833.33, AV=5060, SP=5060/3833.33≈1.32
    expected_new_shares = 5000.0 / expected_sp_t3
    expected_total_supply = expected_supply_after_reset + expected_new_shares
    assert combined.iloc[4]["total_supply"] == pytest.approx(expected_total_supply)
    assert combined.iloc[4]["share_price"] == pytest.approx(expected_sp_t3, rel=0.01)

    # T5: PnL +200, AV=5260, supply unchanged, SP rises
    assert combined.iloc[5]["total_assets"] == pytest.approx(5260.0)
    assert combined.iloc[5]["share_price"] == pytest.approx(5260.0 / expected_total_supply)

    # Key invariant: share price never exceeds reasonable bounds
    assert combined["share_price"].max() < SHARE_PRICE_RESET_THRESHOLD
    assert (combined["share_price"] > 0).all()
    assert (combined["share_price"] < 10.0).all()

    # epoch_reset column should exist and only be True at T2
    assert "epoch_reset" in combined.columns
    assert combined["epoch_reset"].sum() == 1


def test_offline_share_price_recomputation(tmp_path):
    """Test offline share price recomputation from stored DuckDB data.

    Simulates the scenario where a DuckDB was built with the old reset-to-1.0
    logic, then recomputed offline with the new chain-linked logic.

    Creates a DuckDB with known broken data (share_price resets to 1.0 at epoch
    boundary), runs ``recompute_vault_share_prices()``, and verifies that:

    - Share prices are now chain-linked (no jumps to 1.0)
    - ``epoch_reset`` column is correctly set
    - ``daily_return`` is recalculated from new share prices
    - ``detect_broken_vaults()`` finds the issues before healing

    The test data is carefully constructed so that the reconstructed netflow
    from stored tvl + daily_pnl exactly burns all shares at day 3, triggering
    an epoch reset in the recomputation.

    Netflow reconstruction formula:
    - ``netflow[0] = tvl[0] - cumulative_pnl[0]``
    - ``netflow[i] = (tvl[i] - tvl[i-1]) - daily_pnl[i]``

    Day-by-day reconstruction:
    - Day 1: netflow = 1000 - 0 = 1000 → mint 1000 shares at SP=1.0
    - Day 2: netflow = (1200-1000) - 200 = 0 → SP = 1200/1000 = 1.2
    - Day 3: netflow = (50-1200) - 50 = -1200 → burn 1200/1.2 = 1000 shares
      → supply=0, AV=50 > 10 → epoch reset, SP carries forward at 1.2
    - Day 4: netflow = (60-50) - 10 = 0 → SP = 60/41.67 ≈ 1.44
    - Day 5: netflow = (5060-60) - 0 = 5000 → mint at ~1.44
    - Day 6: netflow = (5260-5060) - 200 = 0 → SP rises
    """
    import datetime

    from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase

    db_path = tmp_path / "test-heal.duckdb"
    db = HyperliquidDailyMetricsDatabase(db_path)

    try:
        vault_address = "0xdeadbeef00000000000000000000000000000001"

        # Insert vault metadata
        db.upsert_vault_metadata(
            vault_address=vault_address,
            name="Test Broken Vault",
            leader="0x0000000000000000000000000000000000000001",
            description=None,
            is_closed=False,
            relationship_type="normal",
            create_time=datetime.datetime(2024, 1, 1),
            commission_rate=0.1,
            follower_count=5,
            tvl=10000.0,
            apr=50.0,
        )

        # Insert "broken" daily prices simulating old reset-to-1.0 behaviour.
        #
        # The data is crafted so that offline netflow reconstruction produces
        # exactly -1200 at day 3, which burns all 1000 shares (at SP=1.2),
        # triggering an epoch reset.
        #
        # Old code: SP reset to 1.0 at day 3 (BROKEN).
        # New code: SP carries forward at 1.2 (chain-linked).
        rows = [
            # (vault_address, date, share_price, tvl, cumulative_pnl, daily_pnl,
            #  daily_return, follower_count, apr, is_closed, allow_deposits,
            #  leader_fraction, leader_commission, dep_count, wd_count, dep_usd, wd_usd, epoch_reset)
            (vault_address, datetime.date(2024, 1, 1), 1.0, 1000.0, 0.0, 0.0, 0.0, 5, 50.0, None, None, None, None, None, None, None, None, None),
            (vault_address, datetime.date(2024, 1, 2), 1.2, 1200.0, 200.0, 200.0, 0.2, 5, 50.0, None, None, None, None, None, None, None, None, None),
            (vault_address, datetime.date(2024, 1, 3), 1.0, 50.0, 250.0, 50.0, -0.1667, 5, 50.0, None, None, None, None, None, None, None, None, None),
            (vault_address, datetime.date(2024, 1, 4), 1.2, 60.0, 260.0, 10.0, 0.2, 5, 50.0, None, None, None, None, None, None, None, None, None),
            (vault_address, datetime.date(2024, 1, 5), 1.2, 5060.0, 260.0, 0.0, 0.0, 5, 50.0, None, None, None, None, None, None, None, None, None),
            (vault_address, datetime.date(2024, 1, 6), 1.2474, 5260.0, 460.0, 200.0, 0.0395, 5, 50.0, True, True, 0.5, 10.0, None, None, None, None, None),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        # Verify detection finds issues: epoch_reset is NULL for all rows
        issues = db.detect_broken_vaults()
        assert len(issues) > 0, "Should detect at least one issue"
        null_epoch_issues = issues[issues["issue_type"] == "missing_epoch_reset"]
        assert len(null_epoch_issues) == 1, "Should detect missing epoch_reset"
        assert null_epoch_issues.iloc[0]["vault_address"] == vault_address

        # Recompute share prices
        result = db.recompute_vault_share_prices(vault_address)
        db.save()

        assert result["rows"] == 6
        assert result["changed_rows"] > 0, "At least some rows should change"
        assert result["epoch_resets"] >= 1, "Should detect epoch reset at day 3"

        # Verify the healed data
        healed = db.get_vault_daily_prices(vault_address)

        # Day 1: SP should still be 1.0 (initial deposit)
        assert healed.iloc[0]["share_price"] == pytest.approx(1.0, abs=0.01)

        # Day 2: SP should be 1.2 (PnL +200, AV=1200, supply=1000)
        assert healed.iloc[1]["share_price"] == pytest.approx(1.2, abs=0.01)

        # Day 3: Chain-linked reset — SP should carry forward at 1.2, NOT reset to 1.0
        assert healed.iloc[2]["share_price"] == pytest.approx(1.2, abs=0.05)
        assert healed.iloc[2]["share_price"] != pytest.approx(1.0, abs=0.01), "Share price should NOT be 1.0 (chain-linked reset should carry forward)"
        assert healed.iloc[2]["epoch_reset"] == True

        # Day 4: SP should reflect PnL on the carried-forward basis (~1.44)
        assert healed.iloc[3]["share_price"] > healed.iloc[2]["share_price"]
        assert healed.iloc[3]["epoch_reset"] == False

        # Key invariant: no share price jumps to exactly 1.0 after the first row
        for i in range(1, len(healed)):
            if healed.iloc[i]["epoch_reset"]:
                continue
            assert healed.iloc[i]["share_price"] != pytest.approx(1.0, abs=0.001)

        # Verify detection no longer finds missing_epoch_reset
        post_issues = db.detect_broken_vaults()
        post_null_epoch = post_issues[post_issues["issue_type"] == "missing_epoch_reset"]
        null_for_this_vault = post_null_epoch[post_null_epoch["vault_address"] == vault_address]
        assert len(null_for_this_vault) == 0, "epoch_reset should now be populated"

    finally:
        db.close()
