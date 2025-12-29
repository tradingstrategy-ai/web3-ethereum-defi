"""Position analysis and DataFrame creation for Hyperliquid vault positions.

This module provides functionality to convert position event history into
a pandas DataFrame format suitable for analysis and visualization.

Example::

    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.position import (
        fetch_vault_fills,
        reconstruct_position_history,
    )
    from eth_defi.hyperliquid.position_analysis import create_account_dataframe

    session = create_hyperliquid_session()
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    fills = fetch_vault_fills(session, vault_address)
    events = reconstruct_position_history(fills)
    df = create_account_dataframe(events)

    # Calculate total account PnL at each timestamp
    pnl_columns = [col for col in df.columns if col.endswith("_pnl")]
    df["total_pnl"] = df[pnl_columns].sum(axis=1)
"""

import datetime
from collections import defaultdict
from decimal import Decimal
from typing import Iterable

import pandas as pd

from eth_defi.hyperliquid.position import PositionDirection, PositionEvent


def create_account_dataframe(events: Iterable[PositionEvent]) -> pd.DataFrame:
    """Create a DataFrame from position events with exposure and PnL columns per market.

    Creates a time-indexed DataFrame where each row represents a point in time
    when a position event occurred. For each market (coin), the DataFrame contains
    columns for both long and short directions tracking exposure and cumulative PnL.

    Column naming convention:
    - ``{coin}_long_exposure``: Long position exposure (positive = size * price)
    - ``{coin}_long_pnl``: Cumulative realized PnL from long positions
    - ``{coin}_short_exposure``: Short position exposure (positive = abs(size) * price)
    - ``{coin}_short_pnl``: Cumulative realized PnL from short positions

    The total account PnL at any row can be calculated by summing all ``*_pnl`` columns.

    :param events:
        Iterator of position events from :py:func:`~eth_defi.hyperliquid.position.reconstruct_position_history`.
        Events should be in chronological order.
    :return:
        DataFrame with timestamp index and columns for each market/direction combination.
        Exposure represents the notional value (size * price) of open positions.
        PnL columns contain cumulative realized PnL.
    """
    # Track cumulative state per coin per direction
    # Structure: {coin: {direction: {'exposure': Decimal, 'cumulative_pnl': Decimal}}}
    state: dict[str, dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: {
            "long": {"exposure": Decimal("0"), "cumulative_pnl": Decimal("0")},
            "short": {"exposure": Decimal("0"), "cumulative_pnl": Decimal("0")},
        }
    )

    # Collect rows for DataFrame
    rows: list[dict] = []
    timestamps: list[datetime.datetime] = []

    for event in events:
        coin = event.coin
        direction = "long" if event.direction == PositionDirection.long else "short"

        # Update exposure based on position_after
        # For the given direction, calculate notional exposure
        if event.direction == PositionDirection.long:
            # Long position: positive position_after
            state[coin]["long"]["exposure"] = abs(event.position_after) * event.price
            # If position closed (position_after == 0), exposure becomes 0
        else:
            # Short position: negative position_after
            state[coin]["short"]["exposure"] = abs(event.position_after) * event.price

        # Update cumulative PnL if there's realized PnL
        if event.realized_pnl is not None:
            state[coin][direction]["cumulative_pnl"] += event.realized_pnl

        # Build row snapshot with current state for all coins
        row = {}
        for c, directions in state.items():
            for d in ("long", "short"):
                row[f"{c}_{d}_exposure"] = float(directions[d]["exposure"])
                row[f"{c}_{d}_pnl"] = float(directions[d]["cumulative_pnl"])

        rows.append(row)
        timestamps.append(event.timestamp)

    if not rows:
        return pd.DataFrame()

    # Create DataFrame
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))

    # Fill NaN with 0 for columns that didn't exist yet in earlier rows
    df = df.fillna(0.0)

    # Sort columns for consistent ordering
    df = df.reindex(sorted(df.columns), axis=1)

    return df
