"""Export Lighter pool data into the ERC-4626 pipeline format.

This module bridges the Lighter-specific DuckDB data into the formats
consumed by the existing ERC-4626 vault metrics pipeline:

- Synthetic :py:class:`~eth_defi.vault.vaultdb.VaultRow` entries for the
  :py:class:`~eth_defi.vault.vaultdb.VaultDatabase` pickle
- Raw price DataFrames matching the uncleaned Parquet schema, so that
  Lighter data goes through the same cleaning pipeline as EVM vaults
- Merge functions to append Lighter data into existing files

Example::

    from pathlib import Path
    from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
    from eth_defi.lighter.vault_data_export import merge_into_vault_database, merge_into_uncleaned_parquet

    db = LighterDailyMetricsDatabase(Path("daily-metrics.duckdb"))

    merge_into_vault_database(db, vault_db_path)
    merge_into_uncleaned_parquet(db, uncleaned_parquet_path)

    db.close()

"""

import datetime
import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DENOMINATION, LIGHTER_POOL_FEE_MODE, LIGHTER_POOL_LOCKUP
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def create_lighter_pool_row(
    account_index: int,
    name: str,
    description: str | None,
    tvl: float,
    created_at: datetime.datetime | None,
    operator_fee: float = 0.0,
    is_llp: bool = False,
    status: int = 0,
) -> tuple[VaultSpec, VaultRow]:
    """Create a synthetic VaultRow for a Lighter pool.

    Builds a :py:class:`~eth_defi.vault.vaultdb.VaultRow` that matches what
    :py:func:`~eth_defi.research.vault_metrics.calculate_vault_record` expects,
    using the Lighter synthetic chain ID.

    Lighter pool operator fees are already reflected in the share price
    (internalised skimming model), so the pipeline treats the share price
    as net of fees.

    :param account_index:
        Pool account index on the Lighter platform.
    :param name:
        Pool display name.
    :param description:
        Pool description text.
    :param tvl:
        Current TVL in USDC.
    :param created_at:
        Pool creation timestamp.
    :param operator_fee:
        Operator fee percentage (e.g. 10.0 = 10%).
    :param is_llp:
        Whether this is the LLP protocol pool.
    :param status:
        Pool status code from the API (0 = active).
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = f"lighter-pool-{account_index}"
    chain_id = LIGHTER_CHAIN_ID

    # Convert operator_fee from percentage to decimal fraction
    perf_fee = operator_fee / 100.0 if operator_fee else 0.0

    flags = {VaultFlag.perp_dex_trading_vault}

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=created_at or datetime.datetime(2025, 1, 1),
        features={ERC4626Feature.lighter_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=1,
        redeem_count=0,
    )

    fee_data = FeeData(
        fee_mode=LIGHTER_POOL_FEE_MODE,
        management=0.0,
        performance=perf_fee,
        deposit=0.0,
        withdraw=0.0,
    )

    row: VaultRow = {
        "Symbol": (name or "")[:10],
        "Name": name or "",
        "Address": address,
        "Denomination": LIGHTER_DENOMINATION,
        "Share token": (name or "")[:10],
        "NAV": Decimal(str(tvl)),
        "Shares": Decimal("0"),
        "Protocol": "Lighter",
        "Link": f"https://app.lighter.xyz/public-pools/{account_index}",
        "First seen": created_at,
        "Mgmt fee": 0.0,
        "Perf fee": perf_fee,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": "0x0000000000000000000000000000000000000000", "symbol": "USDC", "decimals": 6},
        "_share_token": None,
        "_fees": fee_data,
        "_flags": flags,
        "_lockup": LIGHTER_POOL_LOCKUP,
        "_description": description,
        "_short_description": description.split(".")[0].strip() + "." if description else None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": f"Pool not active (status {status})" if status != 0 else None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        "_risk": get_vault_risk("Lighter", address),
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def build_raw_prices_dataframe(db: LighterDailyMetricsDatabase) -> pd.DataFrame:
    """Build a raw prices DataFrame from the Lighter DuckDB.

    Produces rows matching the schema of the EVM vault scanner
    (:py:meth:`~eth_defi.vault.base.VaultHistoricalRead.export`),
    so Lighter data can go through the same cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    as ERC-4626 vaults.

    The output has ``timestamp`` as a column (not index), matching
    the raw uncleaned Parquet format.

    :param db:
        The Lighter daily metrics database.
    :return:
        DataFrame with columns matching the uncleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()

    if prices_df.empty:
        return pd.DataFrame()

    chain_id = LIGHTER_CHAIN_ID

    # Build synthetic address from account_index
    addresses = prices_df["account_index"].apply(lambda idx: f"lighter-pool-{idx}")

    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the new index and fills everything with NaN.
    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": addresses.values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values if "tvl" in prices_df.columns else 0.0,
            "total_supply": 0.0,
            "performance_fee": 0.0,
            "management_fee": 0.0,
            "errors": "",
            "written_at": prices_df["written_at"].values if "written_at" in prices_df.columns else pd.NaT,
        },
    )

    # Ensure correct dtypes
    result["chain"] = result["chain"].astype("int32")
    result["block_number"] = result["block_number"].astype("int64")

    return result


def merge_into_vault_database(
    db: LighterDailyMetricsDatabase,
    vault_db_path: Path,
) -> VaultDatabase:
    """Merge Lighter pool metadata into an existing VaultDatabase pickle.

    Reads the existing pickle, upserts Lighter VaultRow entries
    (keyed by VaultSpec), and writes back. Idempotent: running twice
    produces the same result.

    If the pickle file does not exist, creates a new VaultDatabase.

    :param db:
        The Lighter daily metrics database.
    :param vault_db_path:
        Path to the VaultDatabase pickle file.
    :return:
        The updated VaultDatabase.
    """
    if vault_db_path.exists():
        vault_db = VaultDatabase.read(vault_db_path)
    else:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db = VaultDatabase()

    metadata_df = db.get_all_pool_metadata()

    added = 0
    updated = 0
    for _, row in metadata_df.iterrows():
        spec, vault_row = create_lighter_pool_row(
            account_index=int(row["account_index"]),
            name=row["name"],
            description=row.get("description"),
            tvl=row.get("total_asset_value", 0.0) or 0.0,
            created_at=row.get("created_at"),
            operator_fee=row.get("operator_fee", 0.0) or 0.0,
            is_llp=bool(row.get("is_llp", False)),
            status=int(row.get("status", 0) or 0),
        )

        if spec in vault_db.rows:
            updated += 1
        else:
            added += 1

        vault_db.rows[spec] = vault_row

    vault_db.write(vault_db_path)

    logger.info(
        "Merged %d Lighter pools into %s (%d new, %d updated)",
        added + updated,
        vault_db_path,
        added,
        updated,
    )

    return vault_db


def merge_into_uncleaned_parquet(
    db: LighterDailyMetricsDatabase,
    parquet_path: Path,
) -> pd.DataFrame:
    """Merge Lighter daily prices into the uncleaned Parquet file.

    Writes Lighter raw data in the same format as the EVM vault scanner,
    so the standard cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    can process all vaults together.

    Reads the existing Parquet, removes any prior Lighter rows
    (chain == 9998), appends fresh Lighter daily price rows,
    and writes back. Idempotent: running twice produces the same result.

    If the Parquet file does not exist, creates a new one.

    :param db:
        The Lighter daily metrics database.
    :param parquet_path:
        Path to the uncleaned Parquet file
        (typically ``vault-prices-1h.parquet``).
    :return:
        The combined DataFrame.
    """
    lighter_df = build_raw_prices_dataframe(db)

    if lighter_df.empty:
        logger.warning("No Lighter data to merge")
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return pd.DataFrame()

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)

        # Remove any existing Lighter rows
        existing_df = existing_df[existing_df["chain"] != LIGHTER_CHAIN_ID]

        combined = pd.concat([existing_df, lighter_df], ignore_index=True)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = lighter_df

    # Sort for compression efficiency
    combined = combined.sort_values(["chain", "address", "timestamp"])

    combined.to_parquet(parquet_path, compression="zstd")

    lighter_pool_count = lighter_df["address"].nunique()
    logger.info(
        "Merged %d Lighter pools (%d rows) into uncleaned %s",
        lighter_pool_count,
        len(lighter_df),
        parquet_path,
    )

    return combined
