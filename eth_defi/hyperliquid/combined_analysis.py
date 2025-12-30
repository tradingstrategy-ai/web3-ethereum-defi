"""Combined position and deposit analysis for Hyperliquid vaults.

This module provides functionality to combine position PnL data with deposit/withdrawal
data to create a comprehensive view of vault performance including:

- Cumulative account value over time
- Net capital flows (deposits minus withdrawals)
- Trading PnL separated from capital movements

Example::

    from datetime import datetime, timedelta
    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.position import fetch_vault_fills, reconstruct_position_history
    from eth_defi.hyperliquid.position_analysis import create_account_dataframe
    from eth_defi.hyperliquid.deposit import fetch_vault_deposits, create_deposit_dataframe
    from eth_defi.hyperliquid.combined_analysis import analyse_positions_and_deposits

    session = create_hyperliquid_session()
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"
    start_time = datetime.now() - timedelta(days=30)

    # Fetch position data
    fills = fetch_vault_fills(session, vault_address, start_time=start_time)
    events = reconstruct_position_history(fills)
    position_df = create_account_dataframe(events)

    # Fetch deposit data
    deposit_events = fetch_vault_deposits(session, vault_address, start_time=start_time)
    deposit_df = create_deposit_dataframe(list(deposit_events))

    # Combine for comprehensive analysis
    combined_df = analyse_positions_and_deposits(position_df, deposit_df)

    print(f"Final account value: ${combined_df['cumulative_account_value'].iloc[-1]:,.2f}")
"""

import pandas as pd


def analyse_positions_and_deposits(
    position_df: pd.DataFrame,
    deposit_df: pd.DataFrame,
    initial_balance: float = 0.0,
) -> pd.DataFrame:
    """Combine position and deposit DataFrames into a unified timeline.

    This function merges trading activity (positions/PnL) with capital flows
    (deposits/withdrawals) to create a comprehensive view of vault performance.

    The resulting DataFrame contains:

    - ``pnl_update``: Change in realised PnL at this timestamp (from trading)
    - ``netflow_update``: Change in capital at this timestamp (deposits positive, withdrawals negative)
    - ``cumulative_pnl``: Running total of realised trading PnL
    - ``cumulative_netflow``: Running total of capital flows (deposits - withdrawals)
    - ``cumulative_account_value``: Total account value (initial_balance + netflow + pnl)

    The DataFrame is indexed by timestamp and sorted chronologically, combining
    events from both position changes and deposit/withdrawal activity.

    Example::

        from eth_defi.hyperliquid.combined_analysis import analyse_positions_and_deposits

        # Assuming position_df and deposit_df are already created
        combined = analyse_positions_and_deposits(position_df, deposit_df, initial_balance=1000.0)

        # Get final values
        final_pnl = combined['cumulative_pnl'].iloc[-1]
        final_netflow = combined['cumulative_netflow'].iloc[-1]
        final_value = combined['cumulative_account_value'].iloc[-1]

        print(f"Trading PnL: ${final_pnl:,.2f}")
        print(f"Net capital flow: ${final_netflow:,.2f}")
        print(f"Account value: ${final_value:,.2f}")

    :param position_df:
        DataFrame from :py:func:`~eth_defi.hyperliquid.position_analysis.create_account_dataframe`.
        Should have timestamp index and ``*_pnl`` columns for each market/direction.
    :param deposit_df:
        DataFrame from :py:func:`~eth_defi.hyperliquid.deposit.create_deposit_dataframe`.
        Should have timestamp index and ``usdc`` column with deposit/withdrawal amounts.
    :param initial_balance:
        Starting account balance before the analysis period.
        Defaults to 0.0.
    :return:
        DataFrame with unified timeline containing PnL and capital flow metrics.
    """
    # Handle empty inputs
    if position_df.empty and deposit_df.empty:
        return pd.DataFrame(columns=[
            "pnl_update",
            "netflow_update",
            "cumulative_pnl",
            "cumulative_netflow",
            "cumulative_account_value",
        ])

    # Extract PnL updates from position DataFrame
    position_updates = _extract_pnl_updates(position_df)

    # Extract netflow updates from deposit DataFrame
    deposit_updates = _extract_netflow_updates(deposit_df)

    # Combine into unified timeline
    combined = _merge_timelines(position_updates, deposit_updates)

    # Calculate cumulative values
    combined["cumulative_pnl"] = combined["pnl_update"].cumsum()
    combined["cumulative_netflow"] = combined["netflow_update"].cumsum()
    combined["cumulative_account_value"] = (
        initial_balance + combined["cumulative_netflow"] + combined["cumulative_pnl"]
    )

    return combined


def _extract_pnl_updates(position_df: pd.DataFrame) -> pd.DataFrame:
    """Extract PnL changes from position DataFrame.

    :param position_df: Position analysis DataFrame with *_pnl columns
    :return: DataFrame with timestamp index and pnl_update column
    """
    if position_df.empty:
        return pd.DataFrame(columns=["pnl_update"])

    # Find all PnL columns
    pnl_columns = [col for col in position_df.columns if col.endswith("_pnl")]

    if not pnl_columns:
        return pd.DataFrame(columns=["pnl_update"])

    # Calculate total PnL at each timestamp
    total_pnl = position_df[pnl_columns].sum(axis=1)

    # Calculate the change (delta) at each timestamp
    pnl_update = total_pnl.diff().fillna(total_pnl.iloc[0] if len(total_pnl) > 0 else 0)

    return pd.DataFrame({"pnl_update": pnl_update}, index=position_df.index)


def _extract_netflow_updates(deposit_df: pd.DataFrame) -> pd.DataFrame:
    """Extract capital flow changes from deposit DataFrame.

    :param deposit_df: Deposit DataFrame with usdc column
    :return: DataFrame with timestamp index and netflow_update column
    """
    if deposit_df.empty:
        return pd.DataFrame(columns=["netflow_update"])

    if "usdc" not in deposit_df.columns:
        return pd.DataFrame(columns=["netflow_update"])

    # Each row in deposit_df is already a discrete event with its USDC amount
    # Deposits are positive, withdrawals are negative (as per deposit.py convention)
    return pd.DataFrame({"netflow_update": deposit_df["usdc"]}, index=deposit_df.index)


def _merge_timelines(
    position_updates: pd.DataFrame,
    deposit_updates: pd.DataFrame,
) -> pd.DataFrame:
    """Merge position and deposit timelines into a single DataFrame.

    :param position_updates: DataFrame with pnl_update column
    :param deposit_updates: DataFrame with netflow_update column
    :return: Combined DataFrame sorted by timestamp
    """
    # Handle empty DataFrames
    if position_updates.empty and deposit_updates.empty:
        return pd.DataFrame(columns=["pnl_update", "netflow_update"])

    if position_updates.empty:
        result = deposit_updates.copy()
        result["pnl_update"] = 0.0
        return result[["pnl_update", "netflow_update"]]

    if deposit_updates.empty:
        result = position_updates.copy()
        result["netflow_update"] = 0.0
        return result[["pnl_update", "netflow_update"]]

    # Merge with outer join to keep all timestamps
    combined = pd.merge(
        position_updates,
        deposit_updates,
        left_index=True,
        right_index=True,
        how="outer",
    )

    # Fill NaN with 0 (no update at that timestamp)
    combined = combined.fillna(0.0)

    # Sort by timestamp
    combined = combined.sort_index()

    return combined[["pnl_update", "netflow_update"]]


def get_combined_summary(combined_df: pd.DataFrame) -> dict:
    """Generate a summary of combined position and deposit analysis.

    :param combined_df:
        DataFrame from :py:func:`analyse_positions_and_deposits`
    :return:
        Dict with summary statistics
    """
    if combined_df.empty:
        return {
            "total_events": 0,
            "total_pnl": 0.0,
            "total_netflow": 0.0,
            "final_account_value": 0.0,
            "max_account_value": 0.0,
            "min_account_value": 0.0,
            "max_drawdown": 0.0,
            "start_time": None,
            "end_time": None,
        }

    final_pnl = combined_df["cumulative_pnl"].iloc[-1]
    final_netflow = combined_df["cumulative_netflow"].iloc[-1]
    final_value = combined_df["cumulative_account_value"].iloc[-1]
    max_value = combined_df["cumulative_account_value"].max()
    min_value = combined_df["cumulative_account_value"].min()

    # Calculate max drawdown
    running_max = combined_df["cumulative_account_value"].cummax()
    drawdown = combined_df["cumulative_account_value"] - running_max
    max_drawdown = drawdown.min()

    return {
        "total_events": len(combined_df),
        "total_pnl": float(final_pnl),
        "total_netflow": float(final_netflow),
        "final_account_value": float(final_value),
        "max_account_value": float(max_value),
        "min_account_value": float(min_value),
        "max_drawdown": float(max_drawdown),
        "start_time": combined_df.index.min(),
        "end_time": combined_df.index.max(),
    }
