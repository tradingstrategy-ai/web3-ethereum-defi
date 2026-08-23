"""Examine GMX V2 performance from the common vault dataset.

The script reads GM and GLV metadata and price observations, calculates the
same lifetime metrics used by the vault export pipeline, and prints a compact
table ordered by current TVL. It performs no network requests or data writes.

Environment variables:

- ``VAULT_DATABASE``: Vault metadata pickle. Defaults to the common pipeline
  metadata database.
- ``PRICE_DATABASE``: Cleaned or raw vault price Parquet. Defaults to the
  common cleaned price database.
- ``TOKEN_CACHE``: Token metadata SQLite cache used to label accepted deposit
  tokens. Defaults to the shared token cache.
- ``MIN_TVL``: Optional minimum current TVL in USD. Defaults to zero.
- ``LIMIT``: Optional maximum number of table rows after sorting. Zero means
  all rows.
"""

import os
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, get_period_metrics
from eth_defi.token import TokenDetails, TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

GMX_FEATURES = {ERC4626Feature.gmx_gm, ERC4626Feature.gmx_glv}


def _load_gmx_metadata(path: Path) -> VaultDatabase:
    """Load GM and GLV metadata rows from a vault database.

    :param path:
        Vault metadata pickle produced by the scanner or GMX seed script.
    :return:
        A database containing only GM and GLV rows.
    :raises RuntimeError:
        If the database contains no GMX products.
    """

    source = VaultDatabase.read(path)
    rows: dict[VaultSpec, VaultRow] = {}
    for spec, source_row in source.rows.items():
        detection = source_row["_detection_data"]
        if not detection.features & GMX_FEATURES:
            continue
        rows[spec] = source_row

    if not rows:
        raise RuntimeError(f"Vault metadata contains no GMX products: {path}")
    return VaultDatabase(rows=rows)


def _load_gmx_prices(path: Path, vault_db: VaultDatabase) -> pd.DataFrame:
    """Load and prepare collected GMX observations for lifetime metrics.

    Both cleaned pipeline data and raw backfill output are accepted. Raw data
    receives the minimal derived columns normally added by price cleaning.

    :param path:
        Cleaned or raw common vault-price Parquet file.
    :param vault_db:
        GMX-only metadata database defining the accepted identities.
    :return:
        Timestamp-indexed GMX observations with ``id`` and ``event_count``
        columns.
    :raises RuntimeError:
        If no collected GMX observations exist in the file.
    """

    chain_ids = sorted({spec.chain_id for spec in vault_db.rows})
    prices = pd.read_parquet(path, filters=[("chain", "in", chain_ids)])
    prices["address"] = prices["address"].str.lower()
    identities = {(spec.chain_id, spec.vault_address) for spec in vault_db.rows}
    identity_index = pd.MultiIndex.from_arrays((prices["chain"].astype(int), prices["address"]))
    prices = prices[identity_index.isin(pd.MultiIndex.from_tuples(identities))].copy()
    if prices.empty:
        raise RuntimeError(f"Price database contains no collected GMX observations: {path}")

    prices["timestamp"] = pd.to_datetime(prices["timestamp"], utc=True).dt.tz_localize(None)
    prices = prices.sort_values(["chain", "address", "timestamp", "block_number"], kind="stable")
    prices["id"] = prices["chain"].astype(str) + "-" + prices["address"]
    if "event_count" not in prices.columns:
        prices["event_count"] = 0
    prices = prices.set_index("timestamp")
    return prices


def _load_token_symbols(path: Path, vault_db: VaultDatabase) -> dict[tuple[int, str], str]:
    """Read GMX accepted-token symbols from the local token cache.

    Uncached contracts are represented by shortened addresses. This examiner
    deliberately remains read-only and does not fetch token metadata over RPC.

    :param path:
        Shared token metadata SQLite cache.
    :param vault_db:
        GMX metadata containing accepted-deposit-token addresses.
    :return:
        Symbol mapping keyed by chain ID and lowercase token address.
    """

    token_identities = {(spec.chain_id, address.lower()) for spec, row in vault_db.rows.items() for address in row.get("_gmx_accepted_deposit_tokens", ())}
    cache = TokenDiskCache(path)
    try:
        symbols = {}
        for chain_id, address in token_identities:
            key = TokenDetails.generate_cache_key(chain_id, address)
            cached = cache.get(key)
            symbol = cached.get("symbol") if cached else None
            symbols[chain_id, address] = symbol or f"{address[:6]}…{address[-4:]}"
        return symbols
    finally:
        cache.close()


def _format_tokens(spec: VaultSpec, row: VaultRow, symbols: dict[tuple[int, str], str]) -> str:
    """Format one product's accepted deposit tokens.

    :param spec:
        GM or GLV vault identity.
    :param row:
        Scanner metadata containing accepted token addresses.
    :param symbols:
        Locally resolved token symbol mapping.
    :return:
        Slash-separated unique token symbols in catalogue order.
    """

    token_symbols = [symbols[spec.chain_id, address.lower()] for address in row.get("_gmx_accepted_deposit_tokens", ())]
    return "/".join(dict.fromkeys(token_symbols)) or "Unknown"


def _create_performance_table(metrics: pd.DataFrame, vault_db: VaultDatabase, symbols: dict[tuple[int, str], str]) -> pd.DataFrame:
    """Create the requested GMX performance summary table.

    A CAGR is displayed only when ``calculate_lifetime_metrics()`` produced a
    valid lifetime period. In particular, its three-day minimum prevents a
    short backfill sample from being presented as annual performance.

    :param metrics:
        Output from :func:`calculate_lifetime_metrics`.
    :param vault_db:
        GMX product metadata.
    :param symbols:
        Locally resolved accepted-token symbols.
    :return:
        DataFrame with vault name, tokens, TVL, lifetime CAGR, three-month
        CAGR and three-month Sharpe ratio.
    """

    records = []
    for _, metric in metrics.iterrows():
        spec = VaultSpec(int(metric["chain_id"]), metric["address"])
        row = vault_db.rows[spec]
        tokens = _format_tokens(spec, row, symbols)
        name = row.get("Name") or row.get("Symbol") or spec.vault_address
        if name == "GMX Market":
            name = f"GMX Market [{tokens}]"
        lifetime = get_period_metrics(metric["period_results"], "lifetime")
        three_months = get_period_metrics(metric["period_results"], "3M")
        lifetime_cagr = None if lifetime is None or lifetime.error_reason else lifetime.cagr_net if lifetime.cagr_net is not None else lifetime.cagr_gross
        three_months_cagr = None if three_months is None or three_months.error_reason else three_months.cagr_net if three_months.cagr_net is not None else three_months.cagr_gross
        three_months_sharpe = None if three_months is None or three_months.error_reason else three_months.sharpe
        records.append(
            {
                "Vault name": name,
                "Tokens": tokens,
                "TVL": float(metric["current_nav"]),
                "Lifetime CAGR": lifetime_cagr,
                "3M CAGR": three_months_cagr,
                "3M Sharpe": three_months_sharpe,
            },
        )
    return pd.DataFrame(records).sort_values("TVL", ascending=False, kind="stable")


def main() -> None:
    """Calculate and print GMX lifetime and three-month performance."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    pipeline_dir = get_pipeline_data_dir()
    vault_path = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_path = Path(os.environ.get("PRICE_DATABASE", pipeline_dir / "cleaned-vault-prices-1h.parquet")).expanduser()
    token_cache_path = Path(os.environ.get("TOKEN_CACHE", TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)).expanduser()
    min_tvl = float(os.environ.get("MIN_TVL", "0"))
    limit = int(os.environ.get("LIMIT", "0"))
    if min_tvl < 0:
        raise ValueError(f"MIN_TVL must be non-negative, got {min_tvl}")
    if limit < 0:
        raise ValueError(f"LIMIT must be non-negative, got {limit}")

    vault_db = _load_gmx_metadata(vault_path)
    prices = _load_gmx_prices(price_path, vault_db)
    metrics = calculate_lifetime_metrics(prices, vault_db)
    symbols = _load_token_symbols(token_cache_path, vault_db)
    table = _create_performance_table(metrics, vault_db, symbols)
    table = table[table["TVL"] >= min_tvl]
    if limit:
        table = table.head(limit)

    display_table = table.copy()
    display_table["TVL"] = display_table["TVL"].map(lambda value: f"${value:,.0f}")
    display_table["Lifetime CAGR"] = display_table["Lifetime CAGR"].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2%}")
    display_table["3M CAGR"] = display_table["3M CAGR"].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2%}")
    display_table["3M Sharpe"] = display_table["3M Sharpe"].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2f}")
    print(tabulate(display_table, headers="keys", tablefmt="rounded_outline", showindex=False))


if __name__ == "__main__":
    main()
