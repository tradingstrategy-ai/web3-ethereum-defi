"""Prevent reviewed Infinit source gaps from becoming performance observations.

These address-scoped exclusions are applied after generic state filling and
before return calculations. No other protocol's cleaning behaviour changes.
"""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_CHAIN_ID, INFINIT_PRICE_GAPS
from eth_defi.vault.base import VaultSpec

if TYPE_CHECKING:
    from eth_defi.research.vault_metrics import PeriodMetrics


def _timestamps(frame: pd.DataFrame) -> pd.Series:
    """Return naive UTC timestamps with exactly the input's row index.

    :param frame: Price frame with timestamp index or column.
    :return: Datetime64 series in frame order.
    """
    values = frame["timestamp"] if "timestamp" in frame else frame.index
    return pd.Series(pd.to_datetime(values).to_numpy(), index=frame.index)


def _product_mask(frame: pd.DataFrame) -> pd.Series:
    """Select the reviewed product without broad protocol-name matches.

    :param frame: Raw or cleaned price frame.
    :return: Boolean mask for the exact chain/address identity.
    """
    if "id" in frame:
        return frame["id"].eq(f"{INFINIT_CHAIN_ID}-{APEX_PRODUCT.address}")
    if "chain" in frame and "address" in frame:
        return frame["chain"].eq(INFINIT_CHAIN_ID) & frame["address"].str.lower().eq(APEX_PRODUCT.address)
    return pd.Series(False, index=frame.index)


def mask_infinit_source_gaps(frame: pd.DataFrame) -> None:
    """Restore explicit missing valuations after generic state filling.

    The original corrupt prices remain in the compressed context evidence.

    :param frame: Price frame, modified in place only for reviewed exclusions.
    :return: None.
    """
    product = _product_mask(frame)
    if not product.any():
        return
    timestamps = _timestamps(frame)
    gaps = pd.Series(False, index=frame.index)
    for start, end in INFINIT_PRICE_GAPS:
        gaps |= timestamps.between(start, end)
    mask = product & gaps
    for column in ("share_price", "raw_share_price", "total_assets"):
        if column in frame:
            frame.loc[mask, column] = np.nan


def mask_infinit_gap_returns(frame: pd.DataFrame, group_column: str = "id", returns_column: str = "returns_1h") -> None:
    """Suppress returns whose preceding observation lies across a source gap.

    Missing-price rows may already have been removed by generic sanitisation.
    Checking actual timestamp intervals still prevents bridging those gaps.

    :param frame: Chronologically grouped price rows with ``returns_1h``.
    :param group_column: Shared cleaner's vault grouping column.
    :param returns_column: Hourly or daily percentage-return column to protect.
    :return: None; affected returns become missing.
    """
    product = _product_mask(frame)
    if not product.any() or returns_column not in frame:
        return
    timestamps = _timestamps(frame)
    groups = frame[group_column] if group_column in frame else frame["chain"].astype(str) + "-" + frame["address"].str.lower()
    previous = timestamps.groupby(groups, sort=False).shift(1)
    crosses = pd.Series(False, index=frame.index)
    for start, end in INFINIT_PRICE_GAPS:
        crosses |= previous.le(end) & timestamps.ge(start)
    frame.loc[product & crosses, returns_column] = np.nan


def apply_period_gap_exclusions(spec: VaultSpec, periods: Sequence["PeriodMetrics"]) -> Iterator["PeriodMetrics"]:
    """Withhold performance statistics whose observed endpoints span a gap.

    TVL and source coverage remain visible. Windows wholly within a valid
    segment retain their statistics; unrelated vaults pass through unchanged.

    :param spec: Exact chain and vault identity.
    :param periods: Calculated lookback metrics before public export.
    :return: Reviewed metrics, with cross-gap performance unavailable.
    """
    reviewed = spec.chain_id == INFINIT_CHAIN_ID and spec.vault_address.lower() == APEX_PRODUCT.address
    for period in periods:
        start, end = period.samples_start_at, period.samples_end_at
        if reviewed and start is not None and end is not None and any(start <= gap_end and end >= gap_start for gap_start, gap_end in INFINIT_PRICE_GAPS):
            yield replace(period, error_reason="Infinit period crosses a reviewed source-price gap", returns_gross=None, returns_net=None, cagr_gross=None, cagr_net=None, volatility=None, sharpe=None, max_drawdown=None)
        else:
            yield period
