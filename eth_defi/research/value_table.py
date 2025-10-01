"""Format key value tables."""

import pandas as pd

import numpy as np


def format_series_as_multi_column_grid(
    series: pd.Series,
    n_cols=3,
) -> pd.DataFrame:
    """Display"""
    assert isinstance(series, pd.Series)
    df = series

    # Reshape into a grid (e.g., 2x4 or 3x3)
    n_rows = len(df) // n_cols + (len(df) % n_cols > 0)

    # Pad with empty values if needed
    padded_data = list(df.items()) + [("", "")] * (n_rows * n_cols - len(df))

    # Reshape into grid
    grid_data = np.array(padded_data).reshape(n_rows, n_cols, 2)

    # Create DataFrame
    columns = []
    for i in range(n_cols):
        columns.extend([f"Metric", f"Value"])

    grid_df = pd.DataFrame(grid_data.reshape(n_rows, n_cols * 2), columns=columns)

    # Apply styling with strong borders between pairs
    def add_pair_borders(styler):
        # Create CSS styles for strong right borders on value columns
        styles = []
        for i in range(n_cols - 1):  # Don't add border after last pair
            value_col_idx = i * 2 + 1  # Value columns are at indices 1, 3, 5, etc.
            styles.append({"selector": f"td:nth-child({value_col_idx + 1})", "props": [("border-right", "3px solid black")]})

        styler.set_table_styles(styles)
        return styler

    styled_df = grid_df.style.pipe(add_pair_borders).hide(axis="index")

    return styled_df
