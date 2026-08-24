"""Examine Rysk Premium name, chain, reported TVL and lifetime CAGR.

The script reads only local metadata, contextual history and raw price data.
It performs no network requests or writes. ``Reported TVL`` is Rysk's
published collateral figure, not full marked option-book NAV; it is formatted
in the pool's collateral token and must not be treated as USD unless that token
is a stablecoin.

The lifetime CAGR uses the final epoch withdrawal-PPS curve. It is a return in
the collateral denomination, not a USD return. A value is omitted when fewer
than two final epochs or fewer than three calendar days are available.

Environment variables:

- ``VAULT_DATABASE``: Vault metadata pickle. Defaults under
  ``PIPELINE_DATA_DIR``.
- ``UNCLEANED_PRICE_DATABASE``: Rysk backfill Parquet. Defaults to the common
  raw vault-price Parquet.
- ``CONTEXT_DATABASE``: Rysk snapshot context DuckDB. Defaults to the shared
  contextual-history database.
- ``LIMIT``: Optional maximum number of rows. Zero means all rows.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pandas as pd
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

RYSK_FEATURE = ERC4626Feature.rysk_premium_like
MINIMUM_CAGR_DAYS = 3
MINIMUM_CAGR_OBSERVATIONS = 2


def _load_rysk_metadata(path: Path) -> dict[VaultSpec, VaultRow]:
    """Load Rysk Premium rows from a common vault metadata database.

    Filtering by the persisted detection feature keeps unrelated protocol rows
    out of the report while retaining the common metadata schema intact.

    :param path:
        Metadata pickle produced by the Rysk backfill or scheduled scanner.
    :return:
        Rysk rows keyed by vault specification.
    :raise RuntimeError:
        If no Rysk Premium products are present.
    """

    source = VaultDatabase.read(path)
    rows = {spec: row for spec, row in source.rows.items() if RYSK_FEATURE in row["_detection_data"].features}
    if not rows:
        message = f"Vault metadata contains no Rysk Premium products: {path}"
        raise RuntimeError(message)
    return rows


def _load_rysk_prices(path: Path, rows: dict[VaultSpec, VaultRow]) -> pd.DataFrame:
    """Load sparse final epoch share-price observations for selected Rysk pools.

    The returned frame contains ``chain`` as integer, lower-case ``address`` as
    string, naive UTC ``timestamp`` as ``datetime64``, integer ``block_number``
    and numeric ``share_price``. Other common Parquet columns are preserved.

    :param path:
        Common raw historical-price Parquet.
    :param rows:
        Rysk metadata rows defining accepted chain/address identities.
    :return:
        Timestamp-normalised observations for selected Rysk pools. May be
        empty when the selected pools have not completed a final epoch yet.
    """

    chain_ids = sorted({spec.chain_id for spec in rows})
    prices = pd.read_parquet(path, filters=[("chain", "in", chain_ids)])
    prices["address"] = prices["address"].str.lower()
    identities = pd.MultiIndex.from_tuples((spec.chain_id, spec.vault_address.lower()) for spec in rows)
    price_identities = pd.MultiIndex.from_arrays((prices["chain"].astype(int), prices["address"]))
    prices = prices[price_identities.isin(identities)].copy()
    if prices.empty:
        return prices
    prices["timestamp"] = pd.to_datetime(prices["timestamp"], utc=True).dt.tz_localize(None)
    prices["share_price"] = pd.to_numeric(prices["share_price"], errors="coerce")
    return prices.dropna(subset=["share_price"]).sort_values(["chain", "address", "timestamp", "block_number"], kind="stable")


def _load_latest_reported_tvls(context_database: Path) -> dict[tuple[int, str], int]:
    """Read Rysk's most recently published collateral TVL per pool.

    The source table stores ``chain_id`` and ``raw_tvl`` as integers and
    ``pool_address`` as a lower-case string. The result deliberately retains
    raw token units for later denomination-aware formatting.

    :param context_database:
        Shared contextual-history DuckDB containing Rysk source snapshots.
    :return:
        Raw TVL mapping by chain and pool address.
    """

    connection = duckdb.connect(str(context_database), read_only=True)
    try:
        records = connection.execute(
            """
            WITH ranked AS (
                SELECT
                    chain_id,
                    pool_address,
                    raw_tvl,
                    row_number() OVER (
                        PARTITION BY chain_id, pool_address
                        ORDER BY block_number DESC, source_id DESC
                    ) AS rank
                FROM rysk_premium_historical_context
                WHERE raw_tvl IS NOT NULL
            )
            SELECT chain_id, pool_address, raw_tvl
            FROM ranked
            WHERE rank = 1
            """
        ).fetchall()
    finally:
        connection.close()
    return {(int(chain_id), address.lower()): int(raw_tvl) for chain_id, address, raw_tvl in records}


def _format_reported_tvl(raw_tvl: int | None, row: VaultRow) -> str:
    """Format a published collateral TVL using local denomination metadata.

    The metadata row may contain an exported token mapping with integer
    ``decimals`` and string ``symbol`` fields; incomplete mappings stay labelled
    as raw quantities rather than being guessed.

    :param raw_tvl:
        Raw Rysk snapshot TVL, or ``None`` when the pool has no snapshot.
    :param row:
        Rysk metadata row with optional denomination-token export.
    :return:
        Human-readable collateral amount or ``N/A``.
    """

    if raw_tvl is None:
        return "N/A"
    token = row.get("_denomination_token")
    if not isinstance(token, dict):
        return f"{raw_tvl:,} raw"
    decimals = token.get("decimals")
    symbol = token.get("symbol") or "token"
    if not isinstance(decimals, int):
        return f"{raw_tvl:,} {symbol} raw"
    return f"{raw_tvl / 10**decimals:,.2f} {symbol}"


def _calculate_cagr(observations: pd.DataFrame) -> float | None:
    """Calculate a collateral-denominated CAGR from final epoch exit prices.

    ``observations`` must be ordered by naive UTC ``timestamp`` and contain a
    positive numeric ``share_price`` column. The result is a decimal annualised
    return, not a percentage-point value.

    :param observations:
        One pool's time-ordered final withdrawal-PPS observations.
    :return:
        Annualised return, or ``None`` for insufficient history.
    """

    if len(observations) < MINIMUM_CAGR_OBSERVATIONS:
        return None
    first = observations.iloc[0]
    last = observations.iloc[-1]
    elapsed_days = (last["timestamp"] - first["timestamp"]).total_seconds() / 86_400
    if elapsed_days < MINIMUM_CAGR_DAYS or first["share_price"] <= 0:
        return None
    return float((last["share_price"] / first["share_price"]) ** (365.25 / elapsed_days) - 1)


def _create_rows(
    prices: pd.DataFrame,
    metadata: dict[VaultSpec, VaultRow],
    reported_tvls: dict[tuple[int, str], int],
) -> Iterator[dict[str, object]]:
    """Yield display records ordered later by reported collateral TVL.

    ``prices`` follows :func:`_load_rysk_prices`; each yielded mapping contains
    string identity/date fields, integer observation counts, optional numeric
    PPS/CAGR values and an internal integer TVL sort key.

    :param prices:
        Sparse final epoch share-price data.
    :param metadata:
        Rysk pool metadata.
    :param reported_tvls:
        Latest raw collateral TVL by pool.
    :return:
        One display record per Rysk pool with price history.
    """

    for spec, row in metadata.items():
        observations = prices[(prices["chain"] == spec.chain_id) & (prices["address"] == spec.vault_address.lower())]
        raw_tvl = reported_tvls.get((spec.chain_id, spec.vault_address.lower()))
        name = row.get("Name") or row.get("_rysk_pool_name") or spec.vault_address
        yield {
            "Name": name,
            "Chain": get_chain_name(spec.chain_id),
            "Reported TVL": _format_reported_tvl(raw_tvl, row),
            "TVL sort value": raw_tvl or 0,
            "Current PPS": observations.iloc[-1]["share_price"] if not observations.empty else None,
            "Lifetime CAGR": _calculate_cagr(observations),
            "Epoch observations": len(observations),
            "First epoch": observations.iloc[0]["timestamp"].date().isoformat() if not observations.empty else "N/A",
            "Last epoch": observations.iloc[-1]["timestamp"].date().isoformat() if not observations.empty else "N/A",
        }


def main() -> None:
    """Print a Rysk Premium performance table ordered by reported TVL.

    Paths and the optional row limit come only from environment variables. The
    table uses :func:`tabulate.tabulate` and labels collateral TVL separately
    from full marked option-book NAV.

    :return:
        None.
    """

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    limit = int(os.environ.get("LIMIT", "0"))
    if limit < 0:
        message = f"LIMIT must be non-negative, got {limit}"
        raise ValueError(message)
    for path in (vault_database, price_database, context_database):
        if not path.exists():
            raise FileNotFoundError(path)

    metadata = _load_rysk_metadata(vault_database)
    prices = _load_rysk_prices(price_database, metadata)
    reported_tvls = _load_latest_reported_tvls(context_database)
    table = pd.DataFrame(_create_rows(prices, metadata, reported_tvls))
    table = table.sort_values("TVL sort value", ascending=False, kind="stable").drop(columns="TVL sort value")
    if limit:
        table = table.head(limit)

    display = table.copy()
    display["Current PPS"] = display["Current PPS"].map(lambda value: "N/A" if pd.isna(value) else f"{value:,.6f}")
    display["Lifetime CAGR"] = display["Lifetime CAGR"].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2%}")
    print(tabulate(display, headers="keys", tablefmt="rounded_outline", showindex=False))
    print("Reported TVL is collateral-only and is not full marked option-book NAV. CAGR is denominated in that collateral token.")


if __name__ == "__main__":
    main()
