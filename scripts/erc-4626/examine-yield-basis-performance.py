"""Report YieldBasis crvUSD and native-token returns side by side.

The examiner performs no network requests or writes.  It joins common raw
Parquet rows to the exact YieldBasis context observation that produced them,
then uses the same endpoint blocks for crvUSD and native-token CAGR.  A missing
exact source row is an error rather than an invitation to choose a nearby
observation.

Environment variables: ``VAULT_DATABASE``, ``PRICE_DATABASE`` (raw or cleaned
common Parquet), ``CONTEXT_DATABASE``, ``MIN_TVL`` and ``LIMIT``.
"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.metrics import PPS_SCALE, staked_ratio, temporary_redemption_discount

#: Common reporting window used for the trailing CAGR columns.
THREE_MONTHS: datetime.timedelta = datetime.timedelta(days=90)

#: Shortest interval for which endpoint annualisation is meaningful.
MINIMUM_CAGR_WINDOW: datetime.timedelta = datetime.timedelta(days=3)


def _cagr(start: Decimal, end: Decimal, elapsed: datetime.timedelta, *, minimum_elapsed: datetime.timedelta = MINIMUM_CAGR_WINDOW) -> Decimal | None:
    """Annualise an endpoint return, returning null for invalid periods.

    :param start:
        Positive value at the start of the period.
    :param end:
        Positive value at the end of the period.
    :param elapsed:
        Time between the two observations.
    :param minimum_elapsed:
        Shortest interval accepted for annualisation.
    :return:
        Decimal CAGR, or ``None`` when an endpoint or interval is invalid.
    """

    if start <= 0 or end <= 0 or elapsed < minimum_elapsed:
        return None
    return (end / start) ** (Decimal("365.25") / Decimal(str(elapsed.total_seconds() / 86_400))) - 1


def _read_context(path: Path) -> pd.DataFrame:
    """Read the direct YieldBasis context table without mutating it."""

    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT block_number, block_timestamp, lt_address,
                   raw_asset_crvusd_price, raw_asset_price_per_share,
                   raw_preview_shares, raw_redemption_assets,
                   raw_effective_supply, raw_staked_supply
            FROM yield_basis_historical_context
            WHERE chain_id = 1
            ORDER BY lower(lt_address), block_number
            """,
        ).fetchall()
    finally:
        connection.close()
    context = pd.DataFrame(rows, columns=("block_number", "block_timestamp", "lt_address", "raw_asset_crvusd_price", "raw_asset_price_per_share", "raw_preview_shares", "raw_redemption_assets", "raw_effective_supply", "raw_staked_supply"))
    if not context.empty:
        context["lt_address"] = context["lt_address"].str.lower()
        context["timestamp"] = pd.to_datetime(context["block_timestamp"], unit="s", utc=True).dt.tz_localize(None)
        # Keep uint256 values as decimal strings. Converting the 1e18-scaled
        # oracle integers to float would silently discard precision before the
        # native-token CAGR and redemption diagnostics are calculated.
        context["block_number"] = pd.to_numeric(context["block_number"], errors="raise")
    return context


def main() -> None:  # noqa: PLR0914
    """Print one dual-CAGR row per reviewed YieldBasis product."""

    pipeline_dir = get_pipeline_data_dir()
    vault_path = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_path = Path(os.environ.get("PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_path = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    min_tvl = Decimal(os.environ.get("MIN_TVL", "0"))
    limit = int(os.environ.get("LIMIT", "0"))
    if min_tvl < 0 or limit < 0:
        message = "MIN_TVL and LIMIT must be non-negative"
        raise ValueError(message)

    vault_database = VaultDatabase.read(vault_path)
    expected = {review.lt_address.lower(): review for review in YIELD_BASIS_ACTIVE_MARKETS.values()}
    rows = {row["_detection_data"].address.lower(): row for row in vault_database.rows.values() if row["_detection_data"].chain == 1 and ERC4626Feature.yield_basis_lt in row["_detection_data"].features}
    if set(rows) != set(expected):
        raise RuntimeError(f"Metadata does not contain exactly the reviewed YieldBasis products: missing={sorted(set(expected) - set(rows))}, extra={sorted(set(rows) - set(expected))}")

    context = _read_context(context_path)
    raw = pd.read_parquet(price_path)
    raw["address"] = raw["address"].str.lower()
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_localize(None)
    raw = raw[(raw["chain"].astype(int) == 1) & raw["address"].isin(expected)].copy()
    records = []
    for address, review in expected.items():
        product = raw[raw["address"] == address].sort_values(["block_number", "timestamp"], kind="stable")
        product_context = context[context["lt_address"] == address].sort_values("block_number")
        if product.duplicated(["chain", "address", "block_number"]).any():
            raise RuntimeError(f"Duplicate raw YieldBasis rows for {address}")
        context_by_block = {int(row.block_number): row for row in product_context.itertuples(index=False)}
        missing_source = sorted({int(block) for block in product["block_number"]} - set(context_by_block))
        if missing_source:
            raise RuntimeError(f"Raw YieldBasis rows lack exact context source for {address}: {missing_source[:5]}")
        if product.empty or product_context.empty:
            records.append((review.market_id, review.asset_symbol, None, None, None, None, None, None, None, 0))
            continue
        first = product.iloc[0]
        last = product.iloc[-1]
        first_context = context_by_block[int(first["block_number"])]
        last_context = context_by_block[int(last["block_number"])]
        elapsed = last["timestamp"] - first["timestamp"]
        crvusd_cagr = _cagr(Decimal(str(first["share_price"])), Decimal(str(last["share_price"])), elapsed)
        native_cagr = _cagr(Decimal(str(first_context.raw_asset_price_per_share)) / PPS_SCALE, Decimal(str(last_context.raw_asset_price_per_share)) / PPS_SCALE, elapsed)
        cutoff = last["timestamp"] - THREE_MONTHS
        preceding_rows = product[product["timestamp"] <= cutoff]
        if preceding_rows.empty:
            three_month_crvusd = None
            three_month_native = None
        else:
            three_month_start = preceding_rows.iloc[-1]
            three_month_elapsed = last["timestamp"] - three_month_start["timestamp"]
            three_month_crvusd = _cagr(Decimal(str(three_month_start["share_price"])), Decimal(str(last["share_price"])), three_month_elapsed, minimum_elapsed=THREE_MONTHS)
            three_month_context = context_by_block[int(three_month_start["block_number"])]
            three_month_native = _cagr(Decimal(str(three_month_context.raw_asset_price_per_share)) / PPS_SCALE, Decimal(str(last_context.raw_asset_price_per_share)) / PPS_SCALE, three_month_elapsed, minimum_elapsed=THREE_MONTHS)
        trd = temporary_redemption_discount(
            None if pd.isna(last_context.raw_preview_shares) else int(last_context.raw_preview_shares),
            None if pd.isna(last_context.raw_redemption_assets) else int(last_context.raw_redemption_assets),
            int(last_context.raw_asset_price_per_share),
            asset_decimals=review.asset_decimals,
        )
        current_staked_ratio = staked_ratio(int(last_context.raw_effective_supply), int(last_context.raw_staked_supply))
        records.append((review.market_id, review.asset_symbol, Decimal(str(last["total_assets"])), crvusd_cagr, native_cagr, three_month_crvusd, three_month_native, trd, current_staked_ratio, len(product)))

    table = pd.DataFrame(records, columns=("Market", "Native token", "Current TVL (crvUSD)", "Lifetime crvUSD CAGR", "Lifetime native CAGR", "3M crvUSD CAGR", "3M native CAGR", "Current TRD", "Staked ratio", "Raw rows"))
    if min_tvl > 0:
        table = table[table["Current TVL (crvUSD)"].map(lambda value: value is not None and Decimal(str(value)) >= min_tvl)]
    table = table.sort_values("Current TVL (crvUSD)", ascending=False, kind="stable", na_position="last")
    if limit:
        table = table.head(limit)
    for column in ("Lifetime crvUSD CAGR", "Lifetime native CAGR", "3M crvUSD CAGR", "3M native CAGR", "Current TRD", "Staked ratio"):
        table[column] = table[column].map(lambda value: "N/A" if value is None or pd.isna(value) else f"{float(value):.2%}")
    table["Current TVL (crvUSD)"] = table["Current TVL (crvUSD)"].map(lambda value: "N/A" if value is None or pd.isna(value) else f"{float(value):,.0f} crvUSD")
    print(tabulate(table, headers="keys", tablefmt="rounded_outline", showindex=False))


if __name__ == "__main__":
    main()
