"""Examine current Rysk pool size and finalised epoch performance.

Names and the current public product set come from Rysk's application
catalogue. ``Reported TVL`` is read from each pool's onchain ``getTVL()`` and
means free plus allocated collateral, not marked option-book NAV. CAGR uses
only locally backfilled ``epochExecuted`` share-price observations.

Environment variables:

- ``UNCLEANED_PRICE_DATABASE``: Common raw vault-price Parquet.
- ``MAX_WORKERS``: Concurrent onchain TVL readers. Defaults to 4.
- ``LIMIT``: Optional maximum rows; zero means all current public pools.
"""

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pandas as pd
from joblib import Parallel, delayed
from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.rysk.api import RyskPremiumPool, fetch_rysk_premium_pools, is_rysk_premium_test_pool
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import get_pipeline_data_dir

MINIMUM_CAGR_DAYS = 3
MINIMUM_CAGR_OBSERVATIONS = 2


def _load_rysk_prices(path: Path, pools: tuple[RyskPremiumPool, ...]) -> pd.DataFrame:
    """Load local final-epoch observations for current public pools.

    The returned frame contains integer ``chain``, lower-case ``address``,
    naive UTC ``timestamp`` and numeric ``share_price`` columns.

    :param path:
        Common raw historical-price Parquet.
    :param pools:
        Current user-facing catalogue products.
    :return:
        Timestamp-normalised observations, possibly empty.
    """

    chain_ids = sorted({pool.chain_id for pool in pools})
    prices = pd.read_parquet(path, filters=[("chain", "in", chain_ids)])
    prices["address"] = prices["address"].str.lower()
    identities = pd.MultiIndex.from_tuples((pool.chain_id, pool.address) for pool in pools)
    price_identities = pd.MultiIndex.from_arrays((prices["chain"].astype(int), prices["address"]))
    prices = prices[price_identities.isin(identities)].copy()
    if prices.empty:
        return prices
    prices["timestamp"] = pd.to_datetime(prices["timestamp"], utc=True).dt.tz_localize(None)
    prices["share_price"] = pd.to_numeric(prices["share_price"], errors="coerce")
    return prices.dropna(subset=["share_price"]).sort_values(["chain", "address", "timestamp", "block_number"], kind="stable")


def _calculate_cagr(observations: pd.DataFrame) -> float | None:
    """Calculate collateral-denominated CAGR from final execution prices.

    :param observations:
        One pool's chronologically ordered observations with naive UTC
        ``timestamp`` and positive numeric ``share_price`` columns.
    :return:
        Decimal annualised return, or ``None`` for insufficient history.
    """

    if len(observations) < MINIMUM_CAGR_OBSERVATIONS:
        return None
    first = observations.iloc[0]
    last = observations.iloc[-1]
    elapsed_days = (last["timestamp"] - first["timestamp"]).total_seconds() / 86_400
    if elapsed_days < MINIMUM_CAGR_DAYS or first["share_price"] <= 0:
        return None
    return float((last["share_price"] / first["share_price"]) ** (365.25 / elapsed_days) - 1)


def _fetch_reported_tvl(pool: RyskPremiumPool, web3_by_chain: dict[int, Web3]) -> tuple[Decimal, str]:
    """Fetch and label one pool's collateral-only onchain TVL.

    :param pool:
        Current Rysk catalogue identity.
    :param web3_by_chain:
        Reused Web3 connections keyed by EVM chain identifier.
    :return:
        Human-readable collateral amount and token symbol.
    """

    vault = RyskVault(web3_by_chain[pool.chain_id], VaultSpec(pool.chain_id, pool.address))
    denomination = vault.denomination_token
    if denomination is None:
        raise RuntimeError(f"Rysk pool {pool.address} has no readable collateral token")
    return vault.fetch_reported_tvl(), denomination.symbol


def _create_rows(
    pools: tuple[RyskPremiumPool, ...],
    prices: pd.DataFrame,
    tvls: dict[tuple[int, str], tuple[Decimal, str]],
) -> Iterator[dict[str, object]]:
    """Yield display records for current public pools.

    :param pools:
        Current public Rysk products.
    :param prices:
        Local final-epoch observations.
    :param tvls:
        Current collateral-only TVL and denomination by pool identity.
    :return:
        One table record per pool.
    """

    for pool in pools:
        observations = prices[(prices["chain"] == pool.chain_id) & (prices["address"] == pool.address)]
        tvl, symbol = tvls[pool.chain_id, pool.address]
        yield {
            "Name": pool.name,
            "Chain": get_chain_name(pool.chain_id),
            "Reported TVL": f"{tvl:,.2f} {symbol}",
            "TVL sort value": tvl,
            "Current PPS": observations.iloc[-1]["share_price"] if not observations.empty else None,
            "Lifetime CAGR": _calculate_cagr(observations),
            "Final epochs": len(observations),
            "First epoch": observations.iloc[0]["timestamp"].date().isoformat() if not observations.empty else "N/A",
            "Last epoch": observations.iloc[-1]["timestamp"].date().isoformat() if not observations.empty else "N/A",
        }


def main() -> None:
    """Print current Rysk pool TVL and finalised-price performance.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    pipeline_dir = get_pipeline_data_dir()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    if not price_database.exists():
        raise FileNotFoundError(price_database)
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    limit = int(os.environ.get("LIMIT", "0"))
    if max_workers <= 0:
        raise ValueError(f"MAX_WORKERS must be positive, got {max_workers}")
    if limit < 0:
        raise ValueError(f"LIMIT must be non-negative, got {limit}")

    pools = tuple(pool for pool in fetch_rysk_premium_pools() if not is_rysk_premium_test_pool(pool))
    prices = _load_rysk_prices(price_database, pools)
    web3_by_chain = {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id)) for chain_id in {pool.chain_id for pool in pools}}
    tvl_values = Parallel(n_jobs=max_workers, backend="threading")(delayed(_fetch_reported_tvl)(pool, web3_by_chain) for pool in pools)
    tvls = {(pool.chain_id, pool.address): value for pool, value in zip(pools, tvl_values, strict=True)}

    table = pd.DataFrame(_create_rows(pools, prices, tvls)).sort_values("TVL sort value", ascending=False, kind="stable").drop(columns="TVL sort value")
    if limit:
        table = table.head(limit)
    table["Current PPS"] = table["Current PPS"].map(lambda value: "N/A" if pd.isna(value) else f"{value:,.6f}")
    table["Lifetime CAGR"] = table["Lifetime CAGR"].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2%}")
    print(tabulate(table, headers="keys", tablefmt="rounded_outline", showindex=False))
    print("Reported TVL is collateral-only, not marked option-book NAV. CAGR is denominated in each pool's collateral token.")


if __name__ == "__main__":
    main()
