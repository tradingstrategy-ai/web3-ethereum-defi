"""Filter Hyperliquid vaults by peak TVL from cleaned price data.

Reads the cleaned vault prices Parquet file to compute peak TVL per
Hyperliquid vault and returns vaults exceeding a given threshold.

This is used by scripts that need to auto-discover interesting vaults
(e.g. trade history sync) without requiring explicit address lists.

Example::

    from eth_defi.hyperliquid.vault_filter import fetch_vaults_by_peak_tvl

    vaults = fetch_vaults_by_peak_tvl(min_peak_tvl=100_000)
    for v in vaults:
        print(f"{v['name']}: peak ${v['peak_tvl']:,.0f}")
"""

import logging
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE

logger = logging.getLogger(__name__)


def fetch_vaults_by_peak_tvl(
    min_peak_tvl: float,
    parquet_path: Path | None = None,
) -> list[dict]:
    """Get Hyperliquid vaults whose peak historical TVL exceeds a threshold.

    Reads the cleaned vault prices Parquet to find the maximum
    ``total_assets`` for each Hyperliquid vault across all time.

    :param min_peak_tvl:
        Minimum peak TVL in USD. Vaults below this are excluded.
    :param parquet_path:
        Path to cleaned vault prices Parquet. Defaults to
        ``~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet``.
    :return:
        List of dicts with ``address``, ``name``, ``peak_tvl``,
        ``current_tvl``, sorted by peak TVL descending.
    :raises FileNotFoundError:
        If the Parquet file does not exist. Run the daily metrics
        pipeline first to generate it.
    """
    if parquet_path is None:
        parquet_path = DEFAULT_RAW_PRICE_DATABASE

    if not parquet_path.exists():
        raise FileNotFoundError(f"Cleaned vault prices not found at {parquet_path}. Run the daily metrics pipeline first: poetry run python scripts/hyperliquid/daily-vault-metrics.py")

    logger.info("Reading cleaned vault prices from %s", parquet_path)
    df = pd.read_parquet(
        parquet_path,
        columns=["chain", "address", "name", "total_assets"],
    )

    # Filter to Hyperliquid vaults only
    hl = df[df["chain"] == HYPERCORE_CHAIN_ID]

    if hl.empty:
        logger.warning("No Hyperliquid vaults found in %s", parquet_path)
        return []

    # Compute peak TVL per vault
    peak = (
        hl.groupby("address")
        .agg(
            peak_tvl=("total_assets", "max"),
            name=("name", "first"),
        )
        .reset_index()
    )

    # Get current (latest) TVL per vault using the timestamp index
    hl_with_ts = hl.reset_index()
    latest_idx = hl_with_ts.groupby("address")["timestamp"].idxmax()
    latest = hl_with_ts.loc[latest_idx, ["address", "total_assets"]].rename(columns={"total_assets": "current_tvl"})
    peak = peak.merge(latest, on="address", how="left")

    # Filter by threshold
    above = peak[peak["peak_tvl"] >= min_peak_tvl].sort_values("peak_tvl", ascending=False)

    result = []
    for _, row in above.iterrows():
        result.append(
            {
                "address": row["address"],
                "name": row["name"],
                "peak_tvl": row["peak_tvl"],
                "current_tvl": row.get("current_tvl", 0) or 0,
            }
        )

    logger.info(
        "Found %d Hyperliquid vaults with peak TVL >= $%s",
        len(result),
        f"{min_peak_tvl:,.0f}",
    )
    return result
