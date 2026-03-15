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

import numpy as np
import pandas as pd
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_PROTOCOL_VAULT_LOCKUP, HYPERLIQUID_USER_VAULT_LOCKUP, HYPERLIQUID_VAULT_FEE_MODE, HYPERLIQUID_VAULT_PERFORMANCE_FEE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


#: If the leader's share of vault capital drops below this threshold,
#: we warn that new deposits may not be accepted because the leader
#: must maintain at least 5% of total vault capital.
#:
#: The threshold is set 0.5% above the Hyperliquid minimum (5%) to
#: give an early warning before deposits are actually blocked.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders-legacy
#: Verified: 2026-03-09
LEADER_FRACTION_WARNING_THRESHOLD: float = 0.055


def _get_deposit_closed_reason(
    is_closed: bool,
    allow_deposits: bool,
    leader_fraction: float | None = None,
    relationship_type: str = "normal",
) -> str | None:
    """Return a descriptive reason why deposits are closed, or ``None`` if open.

    :param is_closed:
        Whether the vault is permanently closed.
    :param allow_deposits:
        Whether the vault currently accepts deposits.
    :param leader_fraction:
        Leader's fraction of total vault capital (e.g. 0.10 = 10%).
        If below :py:data:`LEADER_FRACTION_WARNING_THRESHOLD`, a warning
        is returned even when the vault nominally accepts deposits.
    :param relationship_type:
        Vault relationship type: ``"normal"``, ``"parent"`` (HLP), or ``"child"``.
        HLP parent vault always accepts deposits (with a 4-day lock-up),
        so ``allow_deposits`` from the API is ignored for it.
    """
    if is_closed:
        return "Vault is permanently closed"
    # HLP parent vault always accepts deposits — the API may report
    # allowDeposits=False but deposits are never actually closed.
    if relationship_type == "parent":
        return None
    if not allow_deposits:
        return "Vault deposits disabled by leader"
    if leader_fraction is not None and leader_fraction < LEADER_FRACTION_WARNING_THRESHOLD:
        return "Leader share of the vault capital near allowed Hyperliquid minimum and new capital may not be accepted"
    return None


def create_hyperliquid_vault_row(
    vault_address: HexAddress,
    name: str,
    description: str | None,
    tvl: float,
    create_time: datetime.datetime | None,
    follower_count: int | None = None,
    is_closed: bool = False,
    allow_deposits: bool = True,
    relationship_type: str = "normal",
    leader_fraction: float | None = None,
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
    :param allow_deposits:
        Whether the vault allows deposits.
        A vault can have ``is_closed=False`` but ``allow_deposits=False``.
    :param relationship_type:
        Vault relationship type from the API: ``"normal"`` for user-created
        vaults, ``"parent"`` for HLP, ``"child"`` for HLP sub-vaults.
    :param leader_fraction:
        Leader's fraction of total vault capital (e.g. 0.10 = 10%).
        Used for :py:func:`_get_deposit_closed_reason` to warn when
        close to the Hyperliquid 5% minimum.
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
    risk = None
    if relationship_type == "child":
        flags.add(VaultFlag.subvault)
        risk = VaultTechnicalRisk.blacklisted

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
        "_description": None,
        "_short_description": description,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": _get_deposit_closed_reason(is_closed, allow_deposits, leader_fraction, relationship_type),
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        "_risk": risk,
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def _compute_deposit_closed_reason_column(prices_df: pd.DataFrame) -> pd.Series:
    """Compute per-row deposit_closed_reason from forward-filled vault state columns.

    Uses :py:func:`_get_deposit_closed_reason` with an explicit NaN guard:
    rows where ``is_closed`` or ``allow_deposits`` are still missing after
    forward-fill get ``None`` (unknown state) instead of being misclassified.

    :param prices_df:
        DataFrame with ``is_closed``, ``allow_deposits``, ``leader_fraction``
        columns (forward-filled within each vault group).
    :return:
        Series of ``str | None`` — reason string when deposits are closed,
        ``None`` when deposits are open or state is unknown.
    """
    has_is_closed = "is_closed" in prices_df.columns
    has_allow_deposits = "allow_deposits" in prices_df.columns

    if not has_is_closed or not has_allow_deposits:
        return pd.Series([None] * len(prices_df), index=prices_df.index)

    reasons = []
    for _, row in prices_df.iterrows():
        is_closed = row.get("is_closed")
        allow_deposits = row.get("allow_deposits")

        # NaN is truthy in bool context — guard against missing state
        if pd.isna(is_closed) or pd.isna(allow_deposits):
            reasons.append(None)
            continue

        lf = row.get("leader_fraction")
        reasons.append(
            _get_deposit_closed_reason(
                is_closed=bool(is_closed),
                allow_deposits=bool(allow_deposits),
                leader_fraction=float(lf) if pd.notna(lf) else None,
            )
        )

    return pd.Series(reasons, index=prices_df.index)


def build_raw_prices_dataframe(db: HyperliquidDailyMetricsDatabase) -> pd.DataFrame:
    """Build a raw prices DataFrame from the Hyperliquid DuckDB.

    Produces rows matching the schema of the EVM vault scanner
    (:py:meth:`~eth_defi.vault.base.VaultHistoricalRead.export`),
    so Hypercore data can go through the same cleaning pipeline
    (:py:func:`~eth_defi.research.wrangle_vault_prices.process_raw_vault_scan_data`)
    as ERC-4626 vaults.

    The output has ``timestamp`` as a column (not index), matching
    the raw uncleaned Parquet format.

    Includes per-row ``deposit_closed_reason`` (str or None) and
    ``deposits_open`` (str "true"/"false" or None) columns derived
    from forward-filled ``is_closed``, ``allow_deposits``, and
    ``leader_fraction`` state columns in the DuckDB.

    Also exposes Hyperliquid's raw cumulative account PnL as
    ``account_pnl`` so downstream consumers can compare the website-style
    account PnL against the cleaned share-price based return series.
    ``follower_count`` and ``cumulative_volume`` are exported as scalar
    historical fields when available.

    :param db:
        The Hyperliquid daily metrics database.
    :return:
        DataFrame with columns matching the uncleaned Parquet schema.
    """
    prices_df = db.get_all_daily_prices()

    if prices_df.empty:
        return pd.DataFrame()

    # Forward-fill sparse state columns within each vault so that the
    # latest known is_closed / allow_deposits / leader_fraction propagates
    # to subsequent rows.  Early rows before first observation stay NaN.
    state_cols = ["is_closed", "allow_deposits", "leader_fraction"]
    existing_state_cols = [c for c in state_cols if c in prices_df.columns]
    if existing_state_cols:
        prices_df = prices_df.sort_values(["vault_address", "date"])
        prices_df[existing_state_cols] = prices_df.groupby("vault_address")[existing_state_cols].ffill()

    # Compute deposit_closed_reason per row from forward-filled state.
    deposit_reasons = _compute_deposit_closed_reason_column(prices_df)

    # Derive deposits_open string for backwards compatibility with ERC-4626 column.
    has_state = prices_df["is_closed"].notna() if "is_closed" in prices_df.columns else pd.Series(False, index=prices_df.index)
    deposits_open = pd.Series([None] * len(prices_df), index=prices_df.index, dtype=object)
    deposits_open[has_state & deposit_reasons.isna()] = "true"
    deposits_open[has_state & deposit_reasons.notna()] = "false"

    chain_id = HYPERCORE_CHAIN_ID

    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the new index and fills everything with NaN.
    #
    # leader_fraction values are 0-1 matching the Percent type alias
    # (e.g. 0.05 = 5%).
    result = pd.DataFrame(
        {
            "chain": chain_id,
            "address": prices_df["vault_address"].values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values,
            "account_pnl": prices_df["cumulative_pnl"].values if "cumulative_pnl" in prices_df.columns else np.nan,
            "follower_count": prices_df["follower_count"].values if "follower_count" in prices_df.columns else np.nan,
            "cumulative_volume": prices_df["cumulative_volume"].values if "cumulative_volume" in prices_df.columns else np.nan,
            "total_supply": 0.0,
            "performance_fee": 0.0,
            "management_fee": 0.0,
            "errors": "",
            "deposits_open": deposits_open.values,
            "deposit_closed_reason": deposit_reasons.values,
            "leader_fraction": prices_df["leader_fraction"].values if "leader_fraction" in prices_df.columns else np.nan,
            "leader_commission": prices_df["leader_commission"].values if "leader_commission" in prices_df.columns else np.nan,
            "daily_deposit_count": prices_df["daily_deposit_count"].values if "daily_deposit_count" in prices_df.columns else np.nan,
            "daily_withdrawal_count": prices_df["daily_withdrawal_count"].values if "daily_withdrawal_count" in prices_df.columns else np.nan,
            "daily_deposit_usd": prices_df["daily_deposit_usd"].values if "daily_deposit_usd" in prices_df.columns else np.nan,
            "daily_withdrawal_usd": prices_df["daily_withdrawal_usd"].values if "daily_withdrawal_usd" in prices_df.columns else np.nan,
            "epoch_reset": prices_df["epoch_reset"].values if "epoch_reset" in prices_df.columns else False,
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
    leader_fractions = db.get_latest_leader_fractions()

    added = 0
    updated = 0
    for _, row in metadata_df.iterrows():
        address = row["vault_address"].lower()
        spec, vault_row = create_hyperliquid_vault_row(
            vault_address=row["vault_address"],
            name=row["name"],
            description=row.get("description"),
            tvl=row.get("tvl", 0.0) or 0.0,
            create_time=row.get("create_time"),
            follower_count=row.get("follower_count"),
            is_closed=bool(row.get("is_closed", False)),
            allow_deposits=bool(row.get("allow_deposits", True)),
            relationship_type=row.get("relationship_type", "normal") or "normal",
            leader_fraction=leader_fractions.get(address),
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
