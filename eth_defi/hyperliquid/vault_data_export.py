"""Export Hyperliquid vault data into the ERC-4626 pipeline format.

This module bridges the Hyperliquid-specific DuckDB data into the formats
consumed by the existing ERC-4626 vault metrics pipeline:

- Synthetic :py:class:`~eth_defi.vault.vaultdb.VaultRow` entries for the
  :py:class:`~eth_defi.vault.vaultdb.VaultDatabase` pickle
- Daily price DataFrames matching the cleaned Parquet schema
- Merge functions to append Hyperliquid data into existing files

Example::

    from pathlib import Path
    from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
    from eth_defi.hyperliquid.vault_data_export import merge_into_vault_database, merge_into_cleaned_parquet

    db = HyperliquidDailyMetricsDatabase(Path("daily-metrics.duckdb"))

    merge_into_vault_database(db, vault_db_path)
    merge_into_cleaned_parquet(db, parquet_path)

    db.close()

"""

import datetime
import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature, ERC4262VaultDetection
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_VAULT_FEE_MODE, HYPERLIQUID_VAULT_PERFORMANCE_FEE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def create_hyperliquid_vault_row(
    vault_address: HexAddress,
    name: str,
    description: str | None,
    tvl: float,
    create_time: datetime.datetime | None,
    follower_count: int | None = None,
    is_closed: bool = False,
) -> tuple[VaultSpec, VaultRow]:
    """Create a synthetic VaultRow for a Hyperliquid native vault.

    Builds a :py:class:`~eth_defi.vault.vaultdb.VaultRow` that matches what
    :py:func:`~eth_defi.research.vault_metrics.calculate_vault_record` expects,
    using the Hypercore synthetic chain ID.

    All Hyperliquid vaults use the fixed platform performance fee
    :py:data:`~eth_defi.hyperliquid.constants.HYPERLIQUID_VAULT_PERFORMANCE_FEE`.

    :param vault_address:
        Vault hex address (will be lowercased).
    :param name:
        Vault display name.
    :param description:
        Vault description text.
    :param tvl:
        Current TVL in USD.
    :param create_time:
        Vault creation timestamp.
    :param follower_count:
        Number of vault depositors.
    :param is_closed:
        Whether the vault is closed for new deposits.
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = vault_address.lower()
    chain_id = HYPERCORE_CHAIN_ID

    perf_fee = HYPERLIQUID_VAULT_PERFORMANCE_FEE

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=create_time or datetime.datetime(2024, 1, 1),
        features={ERC4626Feature.hypercore_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=max(follower_count or 0, 1),
        redeem_count=0,
    )

    fee_data = FeeData(
        fee_mode=HYPERLIQUID_VAULT_FEE_MODE,
        management=0.0,
        performance=perf_fee,
        deposit=0.0,
        withdraw=0.0,
    )

    row: VaultRow = {
        "Symbol": (name or "")[:10],
        "Name": name or "",
        "Address": address,
        "Denomination": "USDC",
        "Share token": (name or "")[:10],
        "NAV": Decimal(str(tvl)),
        "Shares": Decimal("0"),
        "Protocol": "Hyperliquid",
        "Link": f"https://app.hyperliquid.xyz/vaults/{address}",
        "First seen": create_time,
        "Mgmt fee": 0.0,
        "Perf fee": perf_fee,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": "0x2000000000000000000000000000000000000000", "symbol": "USDC", "decimals": 6},
        "_share_token": None,
        "_fees": fee_data,
        "_flags": set(),
        "_lockup": None,
        "_description": description,
        "_short_description": description[:200] if description else None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": "Vault deposits closed" if is_closed else None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def build_cleaned_prices_dataframe(db: HyperliquidDailyMetricsDatabase) -> pd.DataFrame:
    """Build a cleaned prices DataFrame from the Hyperliquid DuckDB.

    Produces a DataFrame matching the schema expected by
    :py:func:`~eth_defi.research.vault_metrics.calculate_hourly_returns_for_all_vaults`
    and :py:func:`~eth_defi.research.vault_metrics.calculate_lifetime_metrics`.

    Includes Hypercore-specific columns (``follower_count``, ``apr``,
    ``cumulative_pnl``, ``daily_pnl``) that will be ``NaN`` for EVM vaults.

    :param db:
        The Hyperliquid daily metrics database.
    :return:
        DataFrame with DatetimeIndex and columns matching the cleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()
    metadata_df = db.get_all_vault_metadata()

    if prices_df.empty:
        return pd.DataFrame()

    # Build a name lookup from metadata
    name_lookup = dict(zip(metadata_df["vault_address"], metadata_df["name"]))

    chain_id = HYPERCORE_CHAIN_ID

    # Convert date to datetime for the index
    prices_df["timestamp"] = pd.to_datetime(prices_df["date"])

    # Build the output DataFrame matching cleaned Parquet schema.
    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the DatetimeIndex and fills everything with NaN.
    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": prices_df["vault_address"].values,
            "block_number": 0,
            "share_price": prices_df["share_price"].values,
            "raw_share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values,
            "total_supply": 0.0,
            "performance_fee": 0.0,
            "management_fee": 0.0,
            "errors": "",
            "id": prices_df["vault_address"].apply(lambda a: f"{chain_id}-{a}").values,
            "name": prices_df["vault_address"].map(name_lookup).fillna("<unnamed>").values,
            "event_count": 1,
            "protocol": "Hyperliquid",
            "returns_1h": prices_df["daily_return"].fillna(0.0).values,
            # Hypercore-specific columns
            "follower_count": prices_df["follower_count"].values,
            "apr": prices_df["apr"].values,
            "cumulative_pnl": prices_df["cumulative_pnl"].values,
            "daily_pnl": prices_df["daily_pnl"].values,
        },
        index=pd.DatetimeIndex(prices_df["timestamp"].values, name="timestamp"),
    )

    # Ensure correct dtypes
    result["chain"] = result["chain"].astype("int32")
    result["block_number"] = result["block_number"].astype("int64")

    return result


def merge_into_vault_database(
    db: HyperliquidDailyMetricsDatabase,
    vault_db_path: Path,
) -> VaultDatabase:
    """Merge Hyperliquid vault metadata into an existing VaultDatabase pickle.

    Reads the existing pickle, upserts Hyperliquid VaultRow entries
    (keyed by VaultSpec), and writes back. Idempotent: running twice
    produces the same result.

    If the pickle file does not exist, creates a new VaultDatabase.

    :param db:
        The Hyperliquid daily metrics database.
    :param vault_db_path:
        Path to the VaultDatabase pickle file.
    :return:
        The updated VaultDatabase.
    """
    # Load or create vault database
    if vault_db_path.exists():
        vault_db = VaultDatabase.read(vault_db_path)
    else:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db = VaultDatabase()

    metadata_df = db.get_all_vault_metadata()

    added = 0
    updated = 0
    for _, row in metadata_df.iterrows():
        spec, vault_row = create_hyperliquid_vault_row(
            vault_address=row["vault_address"],
            name=row["name"],
            description=row.get("description"),
            tvl=row.get("tvl", 0.0) or 0.0,
            create_time=row.get("create_time"),
            follower_count=row.get("follower_count"),
            is_closed=bool(row.get("is_closed", False)),
        )

        if spec in vault_db.rows:
            updated += 1
        else:
            added += 1

        vault_db.rows[spec] = vault_row

    vault_db.write(vault_db_path)

    logger.info(
        "Merged %d Hyperliquid vaults into %s (%d new, %d updated)",
        added + updated,
        vault_db_path,
        added,
        updated,
    )

    return vault_db


def merge_into_cleaned_parquet(
    db: HyperliquidDailyMetricsDatabase,
    parquet_path: Path,
) -> pd.DataFrame:
    """Merge Hyperliquid daily prices into an existing cleaned Parquet file.

    Reads the existing Parquet, removes any prior Hypercore rows
    (chain == -999), appends fresh Hyperliquid daily price rows,
    and writes back. Idempotent: running twice produces the same result.

    If the Parquet file does not exist, creates a new one.

    :param db:
        The Hyperliquid daily metrics database.
    :param parquet_path:
        Path to the cleaned Parquet file.
    :return:
        The combined DataFrame.
    """
    hl_df = build_cleaned_prices_dataframe(db)

    if hl_df.empty:
        logger.warning("No Hyperliquid data to merge")
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return pd.DataFrame()

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)
        # Ensure timestamp index
        if not isinstance(existing_df.index, pd.DatetimeIndex):
            if "timestamp" in existing_df.columns:
                existing_df = existing_df.set_index("timestamp")

        # Remove any existing Hypercore rows
        existing_df = existing_df[existing_df["chain"] != HYPERCORE_CHAIN_ID]

        # Add Hypercore-specific columns to existing data if missing
        for col in ("follower_count", "apr", "cumulative_pnl", "daily_pnl"):
            if col not in existing_df.columns:
                existing_df[col] = pd.NA

        combined = pd.concat([existing_df, hl_df])
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = hl_df

    # Sort for compression efficiency
    combined = combined.sort_values(["id", combined.index.name or "timestamp"])

    combined.to_parquet(parquet_path, compression="zstd")

    hl_vault_count = hl_df["id"].nunique()
    logger.info(
        "Merged %d Hyperliquid vaults (%d rows) into %s",
        hl_vault_count,
        len(hl_df),
        parquet_path,
    )

    return combined
