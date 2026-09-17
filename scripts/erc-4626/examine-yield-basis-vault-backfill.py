"""Check YieldBasis context and common raw-Parquet backfill coverage.

The examiner is read-only. It checks the fixed four-product scope, positive
values, exact context linkage, the total-assets identity, duplicate logical
rows and the absence of DuckDB ART constraints. A bounded scheduled scan may
legitimately have no row for a product; set ``REQUIRE_ALL_PRODUCTS=true`` only
after the full historical backfill.
"""

import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.types import Percent
from eth_defi.vault.base import DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.metrics import redemption_usd_price_per_share

#: Allowed floating-point error in ``total_assets = share_price * supply``.
ASSET_IDENTITY_RELATIVE_TOLERANCE: Percent = 1e-8

#: Allowed round-trip error after the exact Decimal context value is stored in
#: the common Parquet schema as a float.
SHARE_PRICE_RELATIVE_TOLERANCE: Decimal = Decimal("1e-8")

#: Context values that must be present and positive.
CONTEXT_VALUE_COLUMNS: tuple[str, ...] = ("raw_asset_crvusd_price", "raw_asset_price_per_share", "raw_effective_supply")

#: Common Parquet values used by the total-assets identity.
RAW_VALUE_COLUMNS: tuple[str, ...] = ("share_price", "total_supply", "total_assets")


def _primary_share_price_is_wrong(row: pd.Series) -> bool:
    """Check one joined raw row against its exact redemption inputs.

    The joined Series must contain ``share_price``, ``raw_preview_shares``,
    ``raw_redemption_assets``, ``raw_asset_crvusd_price`` and
    ``asset_decimals``. Raw uint256 values remain strings until this function
    converts them to integers, avoiding precision loss before the expected
    marginal redemption price is calculated. The fixed generic stablecoin
    conversion assumption is VaultBase metadata and is not part of this curve.

    :param row:
        One common Parquet row joined to its exact-block context source.
    :return:
        ``True`` when inputs are missing or the common price does not reproduce
        the YieldBasis redemption-value-equivalence formula.
    """

    required = ("share_price", "raw_preview_shares", "raw_redemption_assets", "raw_asset_crvusd_price", "asset_decimals")
    if any(pd.isna(row[column]) for column in required):
        return True
    expected = redemption_usd_price_per_share(
        int(row["raw_preview_shares"]),
        int(row["raw_redemption_assets"]),
        int(row["raw_asset_crvusd_price"]),
        asset_decimals=int(row["asset_decimals"]),
    )
    actual = Decimal(str(row["share_price"]))
    denominator = max(abs(expected), Decimal(1))
    return abs(actual - expected) / denominator > SHARE_PRICE_RELATIVE_TOLERANCE


def main() -> None:  # noqa: PLR0914
    """Print structural coverage and exit non-zero for hard failures.

    The read-only check compares the reviewed four-product scope across
    metadata, exact context and common Parquet data. Any missing source,
    invalid raw value, duplicate or formula mismatch contributes to a failing
    exit status after the diagnostic table is printed.

    :return:
        None.
    """

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("PRICE_DATABASE", os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet"))).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    require_all = os.environ.get("REQUIRE_ALL_PRODUCTS", "false").lower() == "true"
    for path in (vault_database, price_database, context_database):
        if not path.exists():
            raise FileNotFoundError(path)

    database = VaultDatabase.read(vault_database)
    expected = {review.lt_address.lower(): review for review in YIELD_BASIS_ACTIVE_MARKETS.values()}
    selected = {row["_detection_data"].address.lower(): row for row in database.rows.values() if row["_detection_data"].chain == 1 and ERC4626Feature.yield_basis_lt in row["_detection_data"].features}
    missing_metadata = sorted(set(expected) - set(selected))
    unexpected_metadata = sorted(set(selected) - set(expected))
    if missing_metadata or unexpected_metadata:
        raise RuntimeError(f"YieldBasis metadata scope mismatch: missing={missing_metadata}, unexpected={unexpected_metadata}")

    connection = duckdb.connect(str(context_database), read_only=True)
    try:
        table_exists = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'yield_basis_historical_context'").fetchone()[0] == 1
        if not table_exists:
            message = "YieldBasis context table is missing"
            raise RuntimeError(message)
        constraints = connection.execute(
            "SELECT table_name, constraint_type FROM duckdb_constraints() WHERE table_name = 'yield_basis_historical_context' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')",
        ).fetchall()
        context_rows = connection.execute(
            "SELECT chain_id, block_number, block_timestamp, lt_address, asset_address, asset_decimals, raw_asset_crvusd_price, raw_asset_price_per_share, raw_preview_shares, raw_redemption_assets, raw_effective_supply FROM yield_basis_historical_context WHERE chain_id = 1",
        ).fetchall()
    finally:
        connection.close()

    context_frame = pd.DataFrame(
        context_rows,
        columns=("chain_id", "block_number", "block_timestamp", "lt_address", "asset_address", "asset_decimals", "raw_asset_crvusd_price", "raw_asset_price_per_share", "raw_preview_shares", "raw_redemption_assets", "raw_effective_supply"),
    )
    if not context_frame.empty:
        context_frame["lt_address"] = context_frame["lt_address"].str.lower()
        context_frame["asset_address"] = context_frame["asset_address"].str.lower()
        # Keep 1e18-scaled uint256 context values as decimal strings. The
        # checks below parse them as integers so a float conversion cannot
        # hide a precision or range error.
        context_frame["block_number"] = pd.to_numeric(context_frame["block_number"], errors="raise")
    raw = pd.read_parquet(price_database)
    raw["address"] = raw["address"].str.lower()
    identities = pd.MultiIndex.from_tuples([(1, address) for address in expected])
    raw_identities = pd.MultiIndex.from_arrays((raw["chain"].astype(int), raw["address"]))
    selected_raw = raw[raw_identities.isin(identities)].copy()

    failures = len(constraints)
    summaries: list[dict[str, object]] = []
    for address, review in expected.items():
        context = context_frame[context_frame["lt_address"] == address] if not context_frame.empty else context_frame
        product_raw = selected_raw[selected_raw["address"] == address].sort_values("block_number")
        duplicate_context = int(context.duplicated(["chain_id", "lt_address", "block_number"]).sum())
        source_asset_mismatch = int((context["asset_address"].fillna("").str.lower() != review.asset_address.lower()).sum())
        decimals_mismatch = int((pd.to_numeric(context["asset_decimals"], errors="coerce") != review.asset_decimals).sum())
        missing_preview = int((context["raw_preview_shares"].isna() | context["raw_redemption_assets"].isna()).sum())
        duplicate_raw = int(product_raw.duplicated(["chain", "address", "block_number"]).sum())
        context_numeric = context[list(CONTEXT_VALUE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
        invalid_context = int((context_numeric.isna() | context_numeric.le(0)).any(axis="columns").sum())
        numeric = product_raw[list(RAW_VALUE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
        invalid_raw = int((numeric.isna() | numeric.le(0)).any(axis="columns").sum())
        if product_raw.empty:
            identity_errors = 0
            missing_source = 0
        else:
            expected_assets = numeric["share_price"] * numeric["total_supply"]
            identity_errors = int(((numeric["total_assets"] - expected_assets).abs() / numeric["total_assets"].abs().clip(lower=1) > ASSET_IDENTITY_RELATIVE_TOLERANCE).sum())
            context_keys = set(context["block_number"].astype(int)) if not context.empty else set()
            missing_source = sum(int(block) not in context_keys for block in product_raw["block_number"])
        if product_raw.empty or context.empty:
            wrong_primary_price = 0
        else:
            # Duplicate source keys are already a reported failure. Keep the
            # latest one for formula checking so the examiner still reaches
            # its summary instead of pandas raising before diagnostics print.
            context_for_join = context.drop_duplicates("block_number", keep="last")
            joined = product_raw.merge(
                context_for_join[["block_number", "asset_decimals", "raw_asset_crvusd_price", "raw_preview_shares", "raw_redemption_assets"]],
                on="block_number",
                how="left",
                validate="many_to_one",
            )
            wrong_primary_price = int(joined.apply(_primary_share_price_is_wrong, axis="columns").sum())
        sub_threshold = 0
        if len(product_raw) > 1:
            prices = pd.to_numeric(product_raw["share_price"], errors="coerce")
            sub_threshold = int((((prices - prices.shift(1)).abs() / prices) <= DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD).sum())
        failures += sum((duplicate_context, source_asset_mismatch, decimals_mismatch, missing_preview, duplicate_raw, invalid_context, invalid_raw, identity_errors, missing_source, wrong_primary_price))
        if require_all and (context.empty or product_raw.empty):
            failures += 1
        summaries.append(
            {
                "Market": review.market_id,
                "Underlying token": review.asset_symbol,
                "Context rows": len(context),
                "Raw rows": len(product_raw),
                "Duplicate context": duplicate_context,
                "Invalid context": invalid_context,
                "Wrong asset source": source_asset_mismatch,
                "Wrong decimals": decimals_mismatch,
                "Missing preview": missing_preview,
                "Duplicate raw": duplicate_raw,
                "Invalid raw": invalid_raw,
                "Missing source": missing_source,
                "Bad primary price": wrong_primary_price,
                "Bad assets identity": identity_errors,
                "Sub-threshold": sub_threshold,
            }
        )

    print(tabulate(summaries, headers="keys", tablefmt="rounded_outline"))
    print(f"YieldBasis context rows: {len(context_frame)}; DuckDB ART constraints: {len(constraints)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
