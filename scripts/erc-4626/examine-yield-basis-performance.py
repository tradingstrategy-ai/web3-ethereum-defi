"""Report YieldBasis redemption-value and underlying-token returns side by side.

The examiner performs no network requests or writes. It joins common raw
Parquet rows to the exact YieldBasis context observation that produced them,
then uses the same endpoint blocks for USD and underlying-token CAGR. Gross USD
returns compare redemption value at both endpoints. Net USD returns model a
new deposit at fundamental PPS and an exit at redemption value, then apply the
fixed generic USD-stablecoin conversion estimate once at each endpoint. The
underlying series uses fundamental PPS. Price impact is deliberately excluded.
A missing exact source row is an error rather than an invitation to choose a
nearby observation.

Environment variables: ``VAULT_DATABASE``, ``PRICE_DATABASE`` (raw or cleaned
common Parquet), ``CONTEXT_DATABASE``, ``MIN_TVL`` and ``LIMIT``.
"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.metrics import LT_SHARE_SCALE, asset_price_per_share, asset_usd_price, estimate_usd_stablecoin_swap_cost, redemption_usd_price_per_share, round_trip_usd_stablecoin_swap_cost, staked_ratio, temporary_redemption_discount, usd_stablecoin_investor_return

#: Common reporting window used for the trailing CAGR columns.
THREE_MONTHS: datetime.timedelta = datetime.timedelta(days=90)

#: Shortest interval for which endpoint annualisation is meaningful.
MINIMUM_CAGR_WINDOW: datetime.timedelta = datetime.timedelta(days=3)

#: Stable output order for the terminal report.
PERFORMANCE_COLUMNS: tuple[str, ...] = (
    "Name",
    "Market",
    "Underlying token",
    "Current TVL (USD)",
    "Lifetime net USD CAGR",
    "Lifetime gross USD CAGR",
    "Lifetime underlying CAGR",
    "3M net USD CAGR",
    "3M gross USD CAGR",
    "3M underlying CAGR",
    "Current TRD",
    "Stablecoin conversion each way",
    "Round-trip conversion cost",
    "Staked ratio",
    "Raw rows",
)

#: Ratio-valued columns formatted as percentages.
PERCENT_COLUMNS: tuple[str, ...] = PERFORMANCE_COLUMNS[4:14]


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


def _net_usd_cagr(
    fundamental_entry: Decimal,
    redemption_exit: Decimal,
    underlying_token: HexAddress,
    elapsed: datetime.timedelta,
    *,
    minimum_elapsed: datetime.timedelta = MINIMUM_CAGR_WINDOW,
) -> Decimal | None:
    """Annualise USD return after fixed entry and exit conversion costs.

    :param fundamental_entry:
        Fundamental USD PPS used to mint shares at entry, excluding TRD.
    :param redemption_exit:
        TRD-inclusive USD redemption value at exit.
    :param underlying_token:
        YieldBasis underlying address used by the token-based cost policy.
    :param elapsed:
        Time between the two observations.
    :param minimum_elapsed:
        Shortest interval accepted for annualisation.
    :return:
        Net CAGR, or ``None`` when the interval is too short.
    """

    if elapsed < minimum_elapsed:
        return None
    net_return = usd_stablecoin_investor_return(fundamental_entry, redemption_exit, underlying_token)
    return _cagr(Decimal(1), Decimal(1) + net_return, elapsed, minimum_elapsed=minimum_elapsed)


def _read_context(path: Path) -> pd.DataFrame:
    """Read the direct YieldBasis context table without mutating it.

    Raw uint256 columns remain decimal strings in the returned frame. Derived
    timestamp and numeric block columns are added for exact endpoint matching.

    :param path:
        YieldBasis historical-context DuckDB path.
    :return:
        Frame containing one row per exact LT/block observation.
    """

    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT block_number, block_timestamp, lt_address, asset_decimals,
                   raw_asset_crvusd_price,
                   raw_asset_price_per_share,
                   raw_preview_shares, raw_redemption_assets,
                   raw_effective_supply, raw_staked_supply
            FROM yield_basis_historical_context
            WHERE chain_id = 1
            ORDER BY lower(lt_address), block_number
            """,
        ).fetchall()
    finally:
        connection.close()
    context = pd.DataFrame(rows, columns=("block_number", "block_timestamp", "lt_address", "asset_decimals", "raw_asset_crvusd_price", "raw_asset_price_per_share", "raw_preview_shares", "raw_redemption_assets", "raw_effective_supply", "raw_staked_supply"))
    if not context.empty:
        context["lt_address"] = context["lt_address"].str.lower()
        context["timestamp"] = pd.to_datetime(context["block_timestamp"], unit="s", utc=True).dt.tz_localize(None)
        # Keep uint256 values as decimal strings. Converting the 1e18-scaled
        # oracle integers to float would silently discard precision before the
        # underlying-token CAGR and redemption diagnostics are calculated.
        context["block_number"] = pd.to_numeric(context["block_number"], errors="raise")
    return context


def main() -> None:  # noqa: PLR0914
    """Print one gross and depositor-net USD, underlying CAGR and TRD row.

    Environment-selected common Parquet and YieldBasis context data are joined
    by exact block. The terminal table is read-only and sorted by current gross
    redemption-value-equivalent TVL.

    :return:
        None.
    """

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
    records: list[dict[str, object]] = []
    for address, review in expected.items():
        product = raw[raw["address"] == address].sort_values(["block_number", "timestamp"], kind="stable")
        product_context = context[context["lt_address"] == address].sort_values("block_number")
        if product.duplicated(["chain", "address", "block_number"]).any():
            raise RuntimeError(f"Duplicate raw YieldBasis rows for {address}")
        context_by_block = {int(row.block_number): row for row in product_context.itertuples(index=False)}
        missing_source = sorted({int(block) for block in product["block_number"]} - set(context_by_block))
        if missing_source:
            raise RuntimeError(f"Raw YieldBasis rows lack exact context source for {address}: {missing_source[:5]}")
        record: dict[str, object] = {
            "Name": rows[address].get("Name", f"yb-LP {review.asset_symbol}"),
            "Market": review.market_id,
            "Underlying token": review.asset_symbol,
            "Raw rows": len(product),
        }
        if product.empty or product_context.empty:
            records.append(record)
            continue
        first = product.iloc[0]
        last = product.iloc[-1]
        first_context = context_by_block[int(first["block_number"])]
        last_context = context_by_block[int(last["block_number"])]
        # CAGR endpoints follow the common sparse Parquet curve. Current TVL,
        # TRD and staking diagnostics instead use the newest exact context row;
        # otherwise a sub-0.1% share-price move could make a "Current TRD"
        # column several hours older than the completed backfill.
        current_context = product_context.iloc[-1]
        elapsed = last["timestamp"] - first["timestamp"]
        first_usd_share_price = Decimal(str(first["share_price"]))
        last_usd_share_price = Decimal(str(last["share_price"]))
        gross_usd_cagr = _cagr(first_usd_share_price, last_usd_share_price, elapsed)
        # The common curve consistently marks both endpoints at redemption
        # value. A new depositor instead mints at fundamental PPS, so the
        # investor-net calculation uses fundamental USD value at entry and the
        # TRD-inclusive redemption value at exit. Apply the fixed 10-bps
        # generic stablecoin conversion exactly once at each endpoint.
        first_fundamental_usd_share_price = asset_price_per_share(int(first_context.raw_asset_price_per_share)) * asset_usd_price(int(first_context.raw_asset_crvusd_price))
        net_usd_cagr = _net_usd_cagr(
            first_fundamental_usd_share_price,
            last_usd_share_price,
            review.asset_address,
            elapsed,
        )
        underlying_cagr = _cagr(asset_price_per_share(int(first_context.raw_asset_price_per_share)), asset_price_per_share(int(last_context.raw_asset_price_per_share)), elapsed)
        cutoff = last["timestamp"] - THREE_MONTHS
        preceding_rows = product[product["timestamp"] <= cutoff]
        if preceding_rows.empty:
            three_month_gross_usd = None
            three_month_net_usd = None
            three_month_underlying = None
        else:
            three_month_start = preceding_rows.iloc[-1]
            three_month_elapsed = last["timestamp"] - three_month_start["timestamp"]
            three_month_context = context_by_block[int(three_month_start["block_number"])]
            three_month_start_share_price = Decimal(str(three_month_start["share_price"]))
            three_month_fundamental_usd_share_price = asset_price_per_share(int(three_month_context.raw_asset_price_per_share)) * asset_usd_price(int(three_month_context.raw_asset_crvusd_price))
            three_month_gross_usd = _cagr(three_month_start_share_price, last_usd_share_price, three_month_elapsed, minimum_elapsed=THREE_MONTHS)
            three_month_net_usd = _net_usd_cagr(
                three_month_fundamental_usd_share_price,
                last_usd_share_price,
                review.asset_address,
                three_month_elapsed,
                minimum_elapsed=THREE_MONTHS,
            )
            three_month_underlying = _cagr(
                asset_price_per_share(int(three_month_context.raw_asset_price_per_share)),
                asset_price_per_share(int(last_context.raw_asset_price_per_share)),
                three_month_elapsed,
                minimum_elapsed=THREE_MONTHS,
            )
        trd = temporary_redemption_discount(
            int(current_context.raw_preview_shares),
            int(current_context.raw_redemption_assets),
            int(current_context.raw_asset_price_per_share),
            asset_decimals=int(current_context.asset_decimals),
        )
        current_share_price = redemption_usd_price_per_share(
            int(current_context.raw_preview_shares),
            int(current_context.raw_redemption_assets),
            int(current_context.raw_asset_crvusd_price),
            asset_decimals=int(current_context.asset_decimals),
        )
        current_total_assets = current_share_price * Decimal(int(current_context.raw_effective_supply)) / LT_SHARE_SCALE
        current_staked_ratio = staked_ratio(int(current_context.raw_effective_supply), int(current_context.raw_staked_supply))
        current_swap_cost = Decimal(str(estimate_usd_stablecoin_swap_cost(review.asset_address)))
        current_round_trip_cost = round_trip_usd_stablecoin_swap_cost(review.asset_address)
        record.update(
            {
                "Current TVL (USD)": current_total_assets,
                "Lifetime net USD CAGR": net_usd_cagr,
                "Lifetime gross USD CAGR": gross_usd_cagr,
                "Lifetime underlying CAGR": underlying_cagr,
                "3M net USD CAGR": three_month_net_usd,
                "3M gross USD CAGR": three_month_gross_usd,
                "3M underlying CAGR": three_month_underlying,
                "Current TRD": trd,
                "Stablecoin conversion each way": current_swap_cost,
                "Round-trip conversion cost": current_round_trip_cost,
                "Staked ratio": current_staked_ratio,
            }
        )
        records.append(record)

    table = pd.DataFrame.from_records(records, columns=PERFORMANCE_COLUMNS)
    if min_tvl > 0:
        numeric_tvl = pd.to_numeric(table["Current TVL (USD)"], errors="coerce")
        table = table[numeric_tvl.notna() & numeric_tvl.ge(float(min_tvl))]
    table = table.sort_values("Current TVL (USD)", ascending=False, kind="stable", na_position="last")
    if limit:
        table = table.head(limit)
    for column in PERCENT_COLUMNS:
        table[column] = table[column].map(lambda value: "N/A" if value is None or pd.isna(value) else f"{float(value):.2%}")
    table["Current TVL (USD)"] = table["Current TVL (USD)"].map(lambda value: "N/A" if value is None or pd.isna(value) else f"${float(value):,.0f}")
    print(tabulate(table, headers="keys", tablefmt="rounded_outline", showindex=False))


if __name__ == "__main__":
    main()
