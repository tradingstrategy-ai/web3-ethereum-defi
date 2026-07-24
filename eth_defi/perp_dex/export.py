"""Final JSON export helpers for generic perpetual DEX vault metrics."""

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd


def build_perp_dex_other_data(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build ``other_data.perp_dex`` from one already-cleaned price row.

    No protocol API or DuckDB lookup occurs here.  Gross, net and concentration
    are derived from the four materialised price fields.

    :param row:
        Latest cleaned price-row mapping.
    :return:
        Additive JSON object, or ``None`` for a non-perp vault.
    :raises ValueError:
        If an available or stale measurement has lost its observation
        timestamp.
    """

    def missing(value: Any) -> bool:
        """Return whether a scalar carries a pandas or ordinary null.

        :param value:
            Scalar value from the cleaned row.
        :return:
            ``True`` when the value is absent.
        """
        return value is None or bool(pd.isna(value))

    status = row.get("perp_position_data_status")
    if missing(status) or status in {"", "not_applicable"}:
        return None

    def number(name: str) -> float | None:
        """Read one finite numeric field from the cleaned row.

        :param name:
            Cleaned Parquet column name.
        :return:
            Finite float, or ``None`` for missing or invalid input.
        """
        value = row.get(name)
        if missing(value):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    long_notional = number("perp_long_notional")
    short_notional = number("perp_short_notional")
    largest_notional = number("perp_largest_position_notional")
    gross = long_notional + short_notional if long_notional is not None and short_notional is not None else None
    net = long_notional - short_notional if long_notional is not None and short_notional is not None else None
    concentration = largest_notional / gross if largest_notional is not None and gross not in {None, 0.0} else None
    count = row.get("perp_open_position_count")
    if missing(count):
        count = None
    else:
        count = int(count)
    # Values may be stale or deliberately aligned to a delayed daily price
    # row. Always export the original measurement time alongside them so
    # consumers can apply their own freshness threshold.
    observed_at = row.get("perp_metrics_observed_at")
    if status in {"available", "stale"} and missing(observed_at):
        msg = f"Perp metric status {status} requires perp_metrics_observed_at"
        raise ValueError(msg)
    quote_asset = row.get("perp_quote_asset")
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(timespec="seconds") if not missing(observed_at) else None,
        "quote_asset": quote_asset if not missing(quote_asset) and quote_asset else None,
        "position_data_status": status,
        "long_notional": long_notional,
        "short_notional": short_notional,
        "gross_notional": gross,
        "net_notional": net,
        "open_position_count": count,
        "largest_position_fraction": concentration,
    }
