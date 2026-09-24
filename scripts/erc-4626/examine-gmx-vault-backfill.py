"""Examine GMX V2 rows produced by a manual or scheduled backfill.

This script is read-only and performs structural checks on the common raw
Parquet file.  It exits with status 1 when duplicate, invalid, inconsistent or
source-observation-less GMX rows are found.

Environment variables ``VAULT_DATABASE``, ``UNCLEANED_PRICE_DATABASE`` and
``CONTEXT_DATABASE`` may override their ``PIPELINE_DATA_DIR`` defaults. Set
``REQUIRE_ALL_PRODUCTS=true`` only after a full-range backfill; sparse bounded
ranges legitimately have no value event for some products.
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

GMX_FEATURES = {ERC4626Feature.gmx_gm, ERC4626Feature.gmx_glv}
ASSET_IDENTITY_RELATIVE_TOLERANCE = 1e-9


def main() -> None:  # noqa: PLR0914
    """Summarise GMX raw coverage and fail when structural checks do not pass."""

    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    context_database = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    require_all_products = os.environ.get("REQUIRE_ALL_PRODUCTS", "false").lower() == "true"
    for path in (vault_database, price_database, context_database):
        if not path.exists():
            raise FileNotFoundError(path)

    vault_db = VaultDatabase.read(vault_database)
    detections = [row["_detection_data"] for row in vault_db.rows.values() if row["_detection_data"].features & GMX_FEATURES]
    if not detections:
        message = "Vault metadata contains no GMX V2 products"
        raise RuntimeError(message)
    identities = {(detection.chain, detection.address.lower()) for detection in detections}
    raw = pd.read_parquet(price_database)
    raw["address"] = raw["address"].str.lower()
    raw_identities = pd.MultiIndex.from_arrays((raw["chain"].astype(int), raw["address"]))
    gmx = raw[raw_identities.isin(pd.MultiIndex.from_tuples(identities))].copy()

    connection = duckdb.connect(str(context_database), read_only=True)
    try:
        context_rows = connection.execute(
            """
            SELECT chain_id, product_address, block_number
            FROM gmx_historical_context
            """
        ).fetchall()
    finally:
        connection.close()
    context_keys = {(int(chain_id), address.lower(), int(block_number)) for chain_id, address, block_number in context_rows}

    duplicate_count = int(gmx.duplicated(["chain", "address", "block_number"]).sum())
    summaries = []
    failures = duplicate_count
    for detection in sorted(detections, key=lambda item: (item.chain, item.address.lower())):
        subset = gmx[(gmx["chain"] == detection.chain) & (gmx["address"] == detection.address.lower())].sort_values("block_number")
        if subset.empty:
            summaries.append((detection.chain, detection.address, 0, "-", "-", "-", 0, 0, 0, 0))
            if require_all_products:
                failures += 1
            continue
        numeric = subset[["share_price", "total_supply", "total_assets"]].apply(pd.to_numeric, errors="coerce")
        invalid = int((numeric.isna() | (numeric <= 0)).any(axis=1).sum())
        expected_assets = numeric["share_price"] * numeric["total_supply"]
        relative_error = (numeric["total_assets"] - expected_assets).abs() / numeric["total_assets"].abs().clip(lower=1)
        inconsistent = int((relative_error > ASSET_IDENTITY_RELATIVE_TOLERANCE).sum())
        missing_source = sum((detection.chain, detection.address.lower(), int(block_number)) not in context_keys for block_number in subset["block_number"])
        previous_share_price = numeric["share_price"].shift(1)
        relative_price_change = (previous_share_price - numeric["share_price"]).abs() / numeric["share_price"]
        redundant = int((relative_price_change <= DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD).sum())
        block_gaps = subset["block_number"].diff().dropna()
        max_gap = int(block_gaps.max()) if not block_gaps.empty else 0
        # The common scanner always retains the first row of each incremental
        # cycle, so a sub-threshold pair can legitimately straddle two cycles.
        # Report these pairs for inspection without treating them as corrupt.
        failures += invalid + inconsistent + missing_source
        summaries.append(
            (
                detection.chain,
                detection.address,
                len(subset),
                int(subset.iloc[0]["block_number"]),
                int(subset.iloc[-1]["block_number"]),
                max_gap,
                invalid,
                inconsistent,
                missing_source,
                redundant,
            ),
        )

    print(
        tabulate(
            summaries,
            headers=("Chain", "Address", "Rows", "First block", "Last block", "Max block gap", "Invalid", "Bad identity", "Missing source", "Sub-threshold"),
            tablefmt="rounded_outline",
        ),
    )
    print(f"GMX duplicate rows: {duplicate_count}; cached source observations: {len(context_keys)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
