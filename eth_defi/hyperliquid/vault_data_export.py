"""Export Hyperliquid vault data into the ERC-4626 pipeline format.

This module bridges the Hyperliquid-specific DuckDB data into the formats
consumed by the existing ERC-4626 vault metrics pipeline:

- Synthetic :py:class:`~eth_defi.vault.vaultdb.VaultRow` entries for the
  :py:class:`~eth_defi.vault.vaultdb.VaultDatabase` pickle
- Raw price DataFrames matching the uncleaned Parquet schema, so that
  Hypercore data goes through the same cleaning pipeline as EVM vaults
- Merge functions to append Hyperliquid data into existing files

Example::

    from pathlib import Path
    from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
    from eth_defi.hyperliquid.vault_data_export import merge_into_vault_database, merge_into_uncleaned_parquet

    db = HyperliquidDailyMetricsDatabase(Path("daily-metrics.duckdb"))

    merge_into_vault_database(db, vault_db_path)
    merge_into_uncleaned_parquet(db, uncleaned_parquet_path)

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
from eth_defi.hyperliquid.constants import (
    HYPERCORE_CHAIN_ID,
    HYPERLIQUID_PROTOCOL_VAULT_LOCKUP,
    HYPERLIQUID_USER_VAULT_LOCKUP,
    HYPERLIQUID_VAULT_FEE_MODE,
    HYPERLIQUID_VAULT_PERFORMANCE_FEE,
)
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
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
    relationship_type: str = "normal",
) -> tuple[VaultSpec, VaultRow]:
    """Create a synthetic VaultRow for a Hyperliquid native vault.

    Builds a :py:class:`~eth_defi.vault.vaultdb.VaultRow` that matches what
    :py:func:`~eth_defi.research.vault_metrics.calculate_vault_record` expects,
    using the Hypercore synthetic chain ID.

    User-created vaults (``relationship_type="normal"``) use the fixed platform
    performance fee
    :py:data:`~eth_defi.hyperliquid.constants.HYPERLIQUID_VAULT_PERFORMANCE_FEE`.
    Protocol vaults (HLP and its children with ``relationship_type="parent"``
    or ``"child"``) have zero fees.

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
    :param relationship_type:
        Vault relationship type from the API: ``"normal"`` for user-created
        vaults, ``"parent"`` for HLP, ``"child"`` for HLP sub-vaults.
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = vault_address.lower()
    chain_id = HYPERCORE_CHAIN_ID

    # Protocol vaults (HLP parent + children) have zero gross fees and 4-day lockup.
    # User-created vaults have the standard 10% leader profit share and 1-day lockup.
    if relationship_type in ("parent", "child"):
        perf_fee = 0.0
        lockup = HYPERLIQUID_PROTOCOL_VAULT_LOCKUP
    else:
        perf_fee = HYPERLIQUID_VAULT_PERFORMANCE_FEE
        lockup = HYPERLIQUID_USER_VAULT_LOCKUP

    flags = {VaultFlag.perp_dex_trading_vault}

    # HLP child sub-vaults are internal system vaults not directly investable by users
    if relationship_type == "child":
        flags.add(VaultFlag.subvault)

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
        "_flags": flags,
        "_lockup": lockup,
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


def build_raw_prices_dataframe(db: HyperliquidDailyMetricsDatabase) -> pd.DataFrame:
    """Build a raw prices DataFrame from the Hyperliquid DuckDB.

    Produces rows matching the schema of the EVM vault scanner
    (:py:meth:`~eth_defi.vault.base.VaultHistoricalRead.export`),
    so Hypercore data can go through the same cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    as ERC-4626 vaults.

    The output has ``timestamp`` as a column (not index), matching
    the raw uncleaned Parquet format.

    :param db:
        The Hyperliquid daily metrics database.
    :return:
        DataFrame with columns matching the uncleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()

    if prices_df.empty:
        return pd.DataFrame()

    chain_id = HYPERCORE_CHAIN_ID

    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the new index and fills everything with NaN.
    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": prices_df["vault_address"].values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values,
            "total_supply": 0.0,
            "performance_fee": 0.0,
            "management_fee": 0.0,
            "errors": "",
        },
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
            relationship_type=row.get("relationship_type", "normal") or "normal",
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


def merge_into_uncleaned_parquet(
    db: HyperliquidDailyMetricsDatabase,
    parquet_path: Path,
) -> pd.DataFrame:
    """Merge Hyperliquid daily prices into the uncleaned Parquet file.

    Writes Hypercore raw data in the same format as the EVM vault scanner,
    so the standard cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    can process all vaults together.

    Reads the existing Parquet, removes any prior Hypercore rows
    (chain == 9999), appends fresh Hyperliquid daily price rows,
    and writes back.  Idempotent: running twice produces the same result.

    If the Parquet file does not exist, creates a new one.

    :param db:
        The Hyperliquid daily metrics database.
    :param parquet_path:
        Path to the uncleaned Parquet file
        (typically ``vault-prices-1h.parquet``).
    :return:
        The combined DataFrame.
    """
    hl_df = build_raw_prices_dataframe(db)

    if hl_df.empty:
        logger.warning("No Hyperliquid data to merge")
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return pd.DataFrame()

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)

        # Remove any existing Hypercore rows
        existing_df = existing_df[existing_df["chain"] != HYPERCORE_CHAIN_ID]

        combined = pd.concat([existing_df, hl_df], ignore_index=True)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = hl_df

    # Sort for compression efficiency
    combined = combined.sort_values(["chain", "address", "timestamp"])

    combined.to_parquet(parquet_path, compression="zstd")

    hl_vault_count = hl_df["address"].nunique()
    logger.info(
        "Merged %d Hyperliquid vaults (%d rows) into uncleaned %s",
        hl_vault_count,
        len(hl_df),
        parquet_path,
    )

    return combined
