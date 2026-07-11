"""Regression tests for nullable values in vault sparklines."""

import numpy as np
import pandas as pd
import pytest

from eth_defi.research.sparkline import export_sparkline_as_png, render_sparkline_gradient


def test_gradient_sparkline_ignores_nullable_share_prices() -> None:
    """Nullable share prices do not reach Matplotlib's y-axis bounds.

    Production Parquet data may contain ``pd.NA`` before the first valid price.
    The renderer must retain finite points and generate an image rather than
    raising the ambiguous-boolean TypeError from Matplotlib.
    """
    index = pd.date_range("2026-07-01", periods=3, freq="D")
    vault_prices_df = pd.DataFrame(
        {
            "share_price": pd.Series([pd.NA, 1.0, 1.01], index=index, dtype="object"),
            "total_assets": [np.nan, 10_000.0, 10_100.0],
        },
        index=index,
    )

    fig = render_sparkline_gradient(vault_prices_df, ffill=False)
    png = export_sparkline_as_png(fig)

    assert png.startswith(b"\x89PNG")


def test_gradient_sparkline_rejects_vault_without_finite_share_prices() -> None:
    """A completely missing price series has no meaningful sparkline."""
    index = pd.date_range("2026-07-01", periods=2, freq="D")
    vault_prices_df = pd.DataFrame(
        {
            "share_price": pd.Series([pd.NA, pd.NA], index=index, dtype="object"),
            "total_assets": [10_000.0, 10_000.0],
        },
        index=index,
    )

    with pytest.raises(ValueError, match="without finite share prices"):
        render_sparkline_gradient(vault_prices_df, ffill=False)
