"""Export Hibachi vault data into the ERC-4626 pipeline format.

This module bridges the Hibachi-specific DuckDB data into the formats
consumed by the existing ERC-4626 vault metrics pipeline:

- Synthetic :py:class:`~eth_defi.vault.vaultdb.VaultRow` entries for the
  :py:class:`~eth_defi.vault.vaultdb.VaultDatabase` pickle
- Raw price DataFrames matching the uncleaned Parquet schema, so that
  Hibachi data goes through the same cleaning pipeline as EVM vaults
- Merge functions to append Hibachi data into existing files

Example::

    from pathlib import Path
    from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
    from eth_defi.hibachi.vault_data_export import merge_into_vault_database, merge_into_uncleaned_parquet

    db = HibachiDailyMetricsDatabase(Path("daily-metrics.duckdb"))

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
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID, HIBACHI_VAULT_FEE_MODE, HIBACHI_VAULT_LOCKUP
from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def _create_short_description(name: str | None, description: str | None) -> str | None:
    """Create a listing description that does not duplicate the vault name.

    Hibachi's ``shortDescription`` API field is the vault display name, so
    using it for both ``Name`` and ``_short_description`` repeats the title
    in vault listings. Use the first sentence of the strategy description
    instead, and return ``None`` if the source text is missing or still
    matches the name.

    :param name:
        Vault display name.
    :param description:
        Long vault strategy description from the Hibachi API.
    :return:
        First strategy sentence, or ``None`` if no distinct short
        description is available.
    """
    if not description:
        return None

    first_sentence = description.split(".", maxsplit=1)[0].strip()
    if not first_sentence:
        return None

    if name and first_sentence.casefold() == name.strip().casefold():
        return None

    return f"{first_sentence}."


def create_hibachi_vault_row(
    vault_id: int,
    symbol: str,
    name: str,
    description: str | None,
    tvl: float,
) -> tuple[VaultSpec, VaultRow]:
    """Create a synthetic VaultRow for a Hibachi native vault.

    Builds a :py:class:`~eth_defi.vault.vaultdb.VaultRow` that matches what
    :py:func:`~eth_defi.research.vault_metrics.calculate_vault_record` expects,
    using the Hibachi synthetic chain ID.

    All Hibachi vault-level fees are zero.
    ``vault_pub_key`` and ``vault_asset_id`` are stored only in the DuckDB
    metadata table for traceability; they are not surfaced in ``VaultRow``.

    :param vault_id:
        Vault ID on the Hibachi platform (e.g. 2, 3).
    :param symbol:
        Short ticker symbol (e.g. ``GAV``).
    :param name:
        Vault display name.
    :param description:
        Vault description text.
    :param tvl:
        Current TVL in USDT.
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = f"hibachi-vault-{vault_id}"
    chain_id = HIBACHI_CHAIN_ID

    flags = {VaultFlag.perp_dex_trading_vault}

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=datetime.datetime(2025, 1, 1),
        features={ERC4626Feature.hibachi_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )

    fee_data = FeeData(
        fee_mode=HIBACHI_VAULT_FEE_MODE,
        management=0.0,
        performance=0.0,
        deposit=0.0,
        withdraw=0.0,
    )

    row: VaultRow = {
        "Symbol": symbol[:10] if symbol else "",
        "Name": name or "",
        "Address": address,
        "Denomination": "USDT",
        "Share token": symbol[:10] if symbol else "",
        "NAV": Decimal(str(tvl)),
        "Shares": Decimal("0"),
        "Protocol": "Hibachi",
        "Link": "https://hibachi.xyz/vaults",
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
        "_lockup": HIBACHI_VAULT_LOCKUP,
        "_description": description,
        "_short_description": _create_short_description(name, description),
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def build_raw_prices_dataframe(db: HibachiDailyMetricsDatabase) -> pd.DataFrame:
    """Build a raw prices DataFrame from the Hibachi DuckDB.

    Produces rows matching the schema of the EVM vault scanner
    (:py:meth:`~eth_defi.vault.base.VaultHistoricalRead.export`),
    so Hibachi data can go through the same cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    as ERC-4626 vaults.

    The output has ``timestamp`` as a column (not index), matching
    the raw uncleaned Parquet format.

    :param db:
        The Hibachi daily metrics database.
    :return:
        DataFrame with columns matching the uncleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()

    if prices_df.empty:
        return pd.DataFrame()

    chain_id = HIBACHI_CHAIN_ID

    # Build synthetic addresses from vault IDs
    addresses = prices_df["vault_id"].apply(lambda vid: f"hibachi-vault-{vid}")

    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": addresses.values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["per_share_price"].values,
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
    db: HibachiDailyMetricsDatabase,
    vault_db_path: Path,
) -> VaultDatabase:
    """Merge Hibachi vault metadata into an existing VaultDatabase pickle.

    Reads the existing pickle, upserts Hibachi VaultRow entries
    (keyed by VaultSpec), and writes back. Idempotent: running twice
    produces the same result.

    If the pickle file does not exist, creates a new VaultDatabase.

    :param db:
        The Hibachi daily metrics database.
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

    metadata_df = db.get_all_vault_metadata()

    added = 0
    updated = 0
    for _, row in metadata_df.iterrows():
        spec, vault_row = create_hibachi_vault_row(
            vault_id=int(row["vault_id"]),
            symbol=row["symbol"],
            name=row.get("short_description") or row["symbol"],
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
        "Merged %d Hibachi vaults into %s (%d new, %d updated)",
        added + updated,
        vault_db_path,
        added,
        updated,
    )

    return vault_db


def merge_into_uncleaned_parquet(
    db: HibachiDailyMetricsDatabase,
    parquet_path: Path,
) -> pd.DataFrame:
    """Merge Hibachi daily prices into the uncleaned Parquet file.

    Writes Hibachi raw data in the same format as the EVM vault scanner,
    so the standard cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    can process all vaults together.

    Reads the existing Parquet, removes any prior Hibachi rows
    (chain == 9997), appends fresh Hibachi daily price rows,
    and writes back. Idempotent: running twice produces the same result.

    If the Parquet file does not exist, creates a new one.

    :param db:
        The Hibachi daily metrics database.
    :param parquet_path:
        Path to the uncleaned Parquet file
        (typically ``vault-prices-1h.parquet``).
    :return:
        The combined DataFrame.
    """
    hibachi_df = build_raw_prices_dataframe(db)

    if hibachi_df.empty:
        logger.warning("No Hibachi data to merge")
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return pd.DataFrame()

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)

        # Remove any existing Hibachi rows
        existing_df = existing_df[existing_df["chain"] != HIBACHI_CHAIN_ID]

        combined = pd.concat([existing_df, hibachi_df], ignore_index=True)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = hibachi_df

    # Sort for compression efficiency
    combined = combined.sort_values(["chain", "address", "timestamp"])

    # Use PyArrow writer to preserve canonical schema types.
    VaultHistoricalRead.write_uncleaned_parquet(combined, parquet_path)

    hibachi_vault_count = hibachi_df["address"].nunique()
    logger.info(
        "Merged %d Hibachi vaults (%d rows) into uncleaned %s",
        hibachi_vault_count,
        len(hibachi_df),
        parquet_path,
    )

    return combined
