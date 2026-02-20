"""Export GRVT vault data into the ERC-4626 pipeline format.

This module bridges the GRVT-specific DuckDB data into the formats
consumed by the existing ERC-4626 vault metrics pipeline:

- Synthetic :py:class:`~eth_defi.vault.vaultdb.VaultRow` entries for the
  :py:class:`~eth_defi.vault.vaultdb.VaultDatabase` pickle
- Raw price DataFrames matching the uncleaned Parquet schema, so that
  GRVT data goes through the same cleaning pipeline as EVM vaults
- Merge functions to append GRVT data into existing files

Example::

    from pathlib import Path
    from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
    from eth_defi.grvt.vault_data_export import merge_into_vault_database, merge_into_uncleaned_parquet

    db = GRVTDailyMetricsDatabase(Path("daily-metrics.duckdb"))

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
from eth_defi.erc_4626.core import ERC4626Feature, ERC4262VaultDetection
from eth_defi.grvt.constants import (
    GRVT_CHAIN_ID,
    GRVT_VAULT_FEE_MODE,
    GRVT_VAULT_LOCKUP,
)
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def create_grvt_vault_row(
    vault_id: str,
    name: str,
    description: str | None,
    tvl: float,
) -> tuple[VaultSpec, VaultRow]:
    """Create a synthetic VaultRow for a GRVT native vault.

    Builds a :py:class:`~eth_defi.vault.vaultdb.VaultRow` that matches what
    :py:func:`~eth_defi.research.vault_metrics.calculate_vault_record` expects,
    using the GRVT chain ID.

    GRVT fees are embedded in the LP token price (internalised skimming),
    so management and performance fee fields are set to zero — they are
    already reflected in the share price.

    :param vault_id:
        Vault string ID on the GRVT platform (e.g. ``VLT:xxx``).
    :param name:
        Vault display name.
    :param description:
        Vault description text.
    :param tvl:
        Current TVL in USDT.
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = vault_id.lower()
    chain_id = GRVT_CHAIN_ID

    flags = {VaultFlag.perp_dex_trading_vault}

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=datetime.datetime(2025, 1, 1),
        features={ERC4626Feature.grvt_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=1,
        redeem_count=0,
    )

    fee_data = FeeData(
        fee_mode=GRVT_VAULT_FEE_MODE,
        management=0.0,
        performance=0.0,
        deposit=0.0,
        withdraw=0.0,
    )

    row: VaultRow = {
        "Symbol": (name or "")[:10],
        "Name": name or "",
        "Address": address,
        "Denomination": "USDT",
        "Share token": (name or "")[:10],
        "NAV": Decimal(str(tvl)),
        "Shares": Decimal("0"),
        "Protocol": "GRVT",
        "Link": "https://grvt.io/exchange/strategies",
        "First seen": datetime.datetime(2025, 1, 1),
        "Mgmt fee": 0.0,
        "Perf fee": 0.0,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": "0x0000000000000000000000000000000000000000", "symbol": "USDT", "decimals": 6},
        "_share_token": None,
        "_fees": fee_data,
        "_flags": flags,
        "_lockup": GRVT_VAULT_LOCKUP,
        "_description": description,
        "_short_description": description[:200] if description else None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def build_raw_prices_dataframe(db: GRVTDailyMetricsDatabase) -> pd.DataFrame:
    """Build a raw prices DataFrame from the GRVT DuckDB.

    Produces rows matching the schema of the EVM vault scanner
    (:py:meth:`~eth_defi.vault.base.VaultHistoricalRead.export`),
    so GRVT data can go through the same cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    as ERC-4626 vaults.

    The output has ``timestamp`` as a column (not index), matching
    the raw uncleaned Parquet format.

    :param db:
        The GRVT daily metrics database.
    :return:
        DataFrame with columns matching the uncleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()

    if prices_df.empty:
        return pd.DataFrame()

    chain_id = GRVT_CHAIN_ID

    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the new index and fills everything with NaN.
    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": prices_df["vault_id"].values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values if "tvl" in prices_df.columns else 0.0,
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
    db: GRVTDailyMetricsDatabase,
    vault_db_path: Path,
) -> VaultDatabase:
    """Merge GRVT vault metadata into an existing VaultDatabase pickle.

    Reads the existing pickle, upserts GRVT VaultRow entries
    (keyed by VaultSpec), and writes back. Idempotent: running twice
    produces the same result.

    If the pickle file does not exist, creates a new VaultDatabase.

    :param db:
        The GRVT daily metrics database.
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
        spec, vault_row = create_grvt_vault_row(
            vault_id=row["vault_id"],
            name=row["name"],
            description=row.get("description"),
            tvl=row.get("tvl", 0.0) or 0.0,
        )

        if spec in vault_db.rows:
            updated += 1
        else:
            added += 1

        vault_db.rows[spec] = vault_row

    vault_db.write(vault_db_path)

    logger.info(
        "Merged %d GRVT vaults into %s (%d new, %d updated)",
        added + updated,
        vault_db_path,
        added,
        updated,
    )

    return vault_db


def merge_into_uncleaned_parquet(
    db: GRVTDailyMetricsDatabase,
    parquet_path: Path,
) -> pd.DataFrame:
    """Merge GRVT daily prices into the uncleaned Parquet file.

    Writes GRVT raw data in the same format as the EVM vault scanner,
    so the standard cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    can process all vaults together.

    Reads the existing Parquet, removes any prior GRVT rows
    (chain == 325), appends fresh GRVT daily price rows,
    and writes back. Idempotent: running twice produces the same result.

    If the Parquet file does not exist, creates a new one.

    :param db:
        The GRVT daily metrics database.
    :param parquet_path:
        Path to the uncleaned Parquet file
        (typically ``vault-prices-1h.parquet``).
    :return:
        The combined DataFrame.
    """
    grvt_df = build_raw_prices_dataframe(db)

    if grvt_df.empty:
        logger.warning("No GRVT data to merge")
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return pd.DataFrame()

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)

        # Remove any existing GRVT rows
        existing_df = existing_df[existing_df["chain"] != GRVT_CHAIN_ID]

        combined = pd.concat([existing_df, grvt_df], ignore_index=True)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = grvt_df

    # Sort for compression efficiency
    combined = combined.sort_values(["chain", "address", "timestamp"])

    combined.to_parquet(parquet_path, compression="zstd")

    grvt_vault_count = grvt_df["address"].nunique()
    logger.info(
        "Merged %d GRVT vaults (%d rows) into uncleaned %s",
        grvt_vault_count,
        len(grvt_df),
        parquet_path,
    )

    return combined
