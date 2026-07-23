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

import numpy as np
import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.lighter.constants import (
    LIGHTER_DEPLOYMENTS,
    LIGHTER_DEPLOYMENTS_BY_SLUG,
    LIGHTER_ETHEREUM,
    LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID,
    LIGHTER_POOL_FEE_MODE,
    LIGHTER_ROBINHOOD,
    LighterAPIConfig,
    identify_lighter_pool_deployment,
)
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.risk import get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def get_lighter_price_deployments(prices_df: pd.DataFrame) -> set[LighterAPIConfig]:
    """Resolve deployments represented by a Lighter raw-price frame.

    The frame must contain synthetic Lighter addresses in its ``address``
    column. Unknown addresses abort the merge because treating an incomplete
    export as a whole-chain replacement could discard the other deployment's
    history now that Ethereum and Robinhood share synthetic chain 9998.

    :param prices_df:
        Lighter raw-price DataFrame with string ``address`` values.
    :return:
        Deployment configurations represented by at least one row.
    """
    assert "address" in prices_df.columns, f"Lighter price frame is missing address column: {prices_df.columns}"
    deployments = prices_df["address"].apply(identify_lighter_pool_deployment)
    unknown_addresses = prices_df.loc[deployments.isna(), "address"].drop_duplicates().tolist()
    if unknown_addresses:
        raise ValueError(f"Cannot identify Lighter deployment for price addresses: {unknown_addresses}")
    return set(deployments)


def create_lighter_pool_row(
    account_index: int,
    name: str,
    description: str | None,
    tvl: float,
    created_at: datetime.datetime | None,
    operator_fee: float = 0.0,
    total_shares: int | None = None,
    operator_shares: int | None = None,
    ownership_updated_at: datetime.datetime | None = None,
    is_llp: bool = False,
    status: int = 0,
    deployment: LighterAPIConfig = LIGHTER_ETHEREUM,
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
        Current TVL in the deployment's collateral currency.
    :param created_at:
        Pool creation timestamp.
    :param operator_fee:
        Operator fee percentage (e.g. 10.0 = 10%).
    :param total_shares:
        Current total pool shares from the Lighter API.
    :param operator_shares:
        Current pool shares owned by the operator.
    :param ownership_updated_at:
        Naive UTC timestamp of the ownership snapshot.
    :param is_llp:
        Whether this is the LLP protocol pool.
    :param status:
        Pool status code from the API (0 = active).
    :param deployment:
        Lighter deployment configuration. Defaults to Ethereum for backwards
        compatibility.
    :return:
        Tuple of (VaultSpec, VaultRow).
    """
    address = deployment.format_pool_address(account_index)
    chain_id = deployment.chain_id

    # Convert operator_fee from percentage to decimal fraction
    perf_fee = operator_fee / 100.0 if operator_fee else 0.0
    operator_share_fraction: Percent | None = operator_shares / total_shares if total_shares and operator_shares is not None else None

    flags = {VaultFlag.perp_dex_trading_vault}

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=created_at or datetime.datetime(2025, 1, 1),
        features={ERC4626Feature.lighter_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
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
        "Denomination": deployment.denomination,
        "Share token": (name or "")[:10],
        "NAV": Decimal(str(tvl)),
        "Shares": Decimal("0"),
        "Protocol": "Lighter",
        "Link": deployment.format_pool_link(account_index),
        "First seen": created_at,
        "Mgmt fee": 0.0,
        "Perf fee": perf_fee,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": "0x0000000000000000000000000000000000000000", "symbol": deployment.denomination, "decimals": 6},
        "_share_token": None,
        "_fees": fee_data,
        "_flags": flags,
        "_lockup": deployment.lockup,
        "_description": description,
        "_short_description": description.split(".")[0].strip() + "." if description else None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": f"Pool not active (status {status})" if status != 0 else None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        # Persist the deployment-specific manual note in VaultDatabase instead
        # of relying only on lifetime-metrics fallback lookup. For now this is
        # important for Lighter on Robinhood because its USDG insurance-fund
        # note must not inherit Ethereum LLP's USDC/LIT staking requirements.
        "_notes": get_notes(address, chain_id=chain_id, protocol_name="Lighter"),
        "_risk": get_vault_risk("Lighter", address),
        "_lighter_operator_shares": operator_shares,
        "_lighter_total_shares": total_shares,
        "_lighter_operator_share_fraction": operator_share_fraction,
        "_lighter_ownership_updated_at": ownership_updated_at,
        # Keep the deployment slug in static vault metadata so the generic
        # lifetime-metrics code does not need to import Lighter configuration.
        # For now this is exported to distinguish Lighter on Robinhood from
        # the original Ethereum deployment.
        "_deployment": deployment.slug,
        # ``chain_id`` above is intentionally the shared synthetic Lighter ID
        # (9998) and must remain the VaultSpec and price-partition namespace.
        # This associated EVM chain ID (1/4663) is additional metadata
        # introduced specifically for Lighter on Robinhood metrics consumers.
        "_deployment_chain_id": deployment.deployment_chain_id,
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    return spec, row


def _derive_daily_flow_columns(
    prices_df: pd.DataFrame,
    current_date: datetime.date,
) -> pd.DataFrame:
    """Derive safe daily cash flows from Lighter cumulative counters.

    A flow value is available only when the current and preceding observations
    are consecutive completed UTC days and the corresponding cumulative counter
    did not decrease. The current UTC day remains provisional, while gaps,
    resets, and source-null counters remain unknown.

    :param prices_df:
        Lighter daily-price rows with cumulative source counters.
    :param current_date:
        Current UTC date, excluded from completed daily flow output.
    :return:
        Copy of ``prices_df`` with daily USD deposit and withdrawal columns.
    """
    group_columns = ["deployment", "account_index"]
    result = prices_df.sort_values([*group_columns, "date"]).copy()
    result["daily_deposit_usd"] = np.nan
    result["daily_withdrawal_usd"] = np.nan

    observation_dates = pd.to_datetime(result["date"])
    group_values = [result[column] for column in group_columns]
    prior_dates = observation_dates.groupby(group_values, sort=False).shift()
    is_completed = observation_dates.dt.date < current_date
    is_consecutive = (observation_dates - prior_dates).eq(pd.Timedelta(days=1))

    for source_column, target_column in [
        ("cumulative_pool_inflow", "daily_deposit_usd"),
        ("cumulative_pool_outflow", "daily_withdrawal_usd"),
    ]:
        values = result[source_column]
        prior_values = values.groupby(group_values, sort=False).shift()
        delta = values - prior_values
        values_known = values.notna() & prior_values.notna()
        valid_delta = is_completed & is_consecutive & values_known & delta.ge(0)
        decreased_counter = is_completed & is_consecutive & values_known & delta.lt(0)

        if decreased_counter.any():
            logger.warning(
                "Lighter %s counter decreased for %d completed daily observations; withholding those flows",
                source_column,
                int(decreased_counter.sum()),
            )

        result[target_column] = delta.where(valid_delta)

    return result


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

    prices_df = _derive_daily_flow_columns(prices_df, current_date=native_datetime_utc_now().date())

    unknown_deployments = set(prices_df["deployment"].unique()) - set(LIGHTER_DEPLOYMENTS_BY_SLUG)
    if unknown_deployments:
        raise ValueError(f"Unknown Lighter deployments in daily metrics database: {sorted(unknown_deployments)}")

    deployments = prices_df["deployment"].apply(LIGHTER_DEPLOYMENTS_BY_SLUG.__getitem__)
    chain_ids = deployments.apply(lambda deployment: deployment.chain_id)
    addresses = prices_df.apply(
        lambda row: LIGHTER_DEPLOYMENTS_BY_SLUG[row["deployment"]].format_pool_address(int(row["account_index"])),
        axis=1,
    )

    # Use .values to strip the DuckDB RangeIndex — otherwise pandas
    # tries to align it with the new index and fills everything with NaN.
    result = pd.DataFrame(
        {
            "chain": chain_ids.values,
            "address": addresses.values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices_df["date"]).values,
            "share_price": prices_df["share_price"].values,
            "total_assets": prices_df["tvl"].values if "tvl" in prices_df.columns else 0.0,
            "total_supply": prices_df["total_shares"].values if "total_shares" in prices_df.columns else np.nan,
            "performance_fee": 0.0,
            "management_fee": 0.0,
            "errors": "",
            # Lighter's public PnL endpoint exposes cumulative monetary
            # counters, not event-level counts. Keep count values unknown.
            "daily_deposit_count": np.nan,
            "daily_withdrawal_count": np.nan,
            "daily_deposit_usd": prices_df["daily_deposit_usd"].values,
            "daily_withdrawal_usd": prices_df["daily_withdrawal_usd"].values,
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

    # The first Robinhood implementation used synthetic chain 9996. Both
    # deployments now share Lighter chain 9998 and are kept unique by their
    # deployment-specific address prefixes. Remove the short-lived legacy
    # VaultSpec rows automatically once Robinhood metadata is present, so the
    # normal metadata merge cannot leave duplicate Robinhood vaults behind.
    has_robinhood_metadata = "robinhood" in set(metadata_df["deployment"]) if not metadata_df.empty else False
    legacy_specs: list[VaultSpec] = []
    if has_robinhood_metadata:
        legacy_specs = [spec for spec in vault_db.rows if spec.chain_id == LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID and str(spec.vault_address).startswith("lighter-pool-robinhood-")]
        for spec in legacy_specs:
            del vault_db.rows[spec]
        if legacy_specs:
            logger.info(
                "Removed %d legacy Lighter Robinhood VaultSpec rows from synthetic chain %d",
                len(legacy_specs),
                LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID,
            )

    added = 0
    updated = 0
    for _, row in metadata_df.iterrows():
        deployment_slug = str(row["deployment"])
        try:
            deployment = LIGHTER_DEPLOYMENTS_BY_SLUG[deployment_slug]
        except KeyError as e:
            raise ValueError(f"Unknown Lighter deployment in pool metadata: {deployment_slug}") from e

        # DuckDB nullable scalar columns arrive through Pandas as NaN/NaT.
        # Normalise them explicitly because ``NaN or 0`` still returns NaN and
        # would make FeeData validation fail during an automatic legacy merge.
        description = row.get("description")
        description = None if pd.isna(description) else str(description)
        created_at = row.get("created_at")
        created_at = None if pd.isna(created_at) else created_at
        tvl = row.get("total_asset_value")
        tvl = 0.0 if pd.isna(tvl) else float(tvl)
        operator_fee = row.get("operator_fee")
        operator_fee = 0.0 if pd.isna(operator_fee) else float(operator_fee)
        status = row.get("status")
        status = 0 if pd.isna(status) else int(status)
        total_shares = row.get("total_shares")
        total_shares = None if pd.isna(total_shares) else int(total_shares)
        operator_shares = row.get("operator_shares")
        operator_shares = None if pd.isna(operator_shares) else int(operator_shares)
        ownership_updated_at = row.get("last_updated")
        ownership_updated_at = None if pd.isna(ownership_updated_at) else ownership_updated_at

        spec, vault_row = create_lighter_pool_row(
            account_index=int(row["account_index"]),
            name=row["name"],
            description=description,
            tvl=tvl,
            created_at=created_at,
            operator_fee=operator_fee,
            total_shares=total_shares,
            operator_shares=operator_shares,
            ownership_updated_at=ownership_updated_at,
            is_llp=bool(row.get("is_llp", False)),
            status=status,
            deployment=deployment,
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

    Reads the existing Parquet, removes prior rows only for deployments present
    in the fresh export, appends fresh Lighter daily price rows, and writes
    back. Idempotent: running twice produces the same result. This
    deployment-scoped replacement preserves Robinhood history during an
    Ethereum-only scan, and vice versa.

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

    fresh_deployments = get_lighter_price_deployments(lighter_df)

    if parquet_path.exists():
        existing_df = pd.read_parquet(parquet_path)

        # Ethereum and Robinhood share chain 9998 but scan independently. Only
        # remove address namespaces represented by this fresh export, otherwise
        # a successful Ethereum scan followed by a failed Robinhood scan would
        # silently erase valid Robinhood history (or vice versa).
        shared_lighter_mask = existing_df["chain"].isin({deployment.chain_id for deployment in LIGHTER_DEPLOYMENTS})
        existing_deployments = existing_df.loc[shared_lighter_mask, "address"].apply(identify_lighter_pool_deployment)
        replace_current_mask = pd.Series(False, index=existing_df.index)
        replace_current_mask.loc[shared_lighter_mask] = existing_deployments.isin(fresh_deployments)

        # Remove the short-lived 9996 partition only when fresh Robinhood data
        # is available to replace it. An Ethereum-only standalone scan must
        # leave legacy Robinhood history intact until Robinhood succeeds.
        replace_legacy_robinhood_mask = existing_df["chain"].eq(LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID) if LIGHTER_ROBINHOOD in fresh_deployments else pd.Series(False, index=existing_df.index)
        existing_df = existing_df[~(replace_current_mask | replace_legacy_robinhood_mask)]

        combined = pd.concat([existing_df, lighter_df], ignore_index=True)
    else:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined = lighter_df

    # Sort for compression efficiency
    combined = combined.sort_values(["chain", "address", "timestamp"])

    # Use PyArrow writer to preserve canonical schema types.
    # pandas.to_parquet() promotes types (e.g. timestamp[ms] -> timestamp[us])
    # which breaks migrate_parquet_schema() on the next EVM scan run.
    VaultHistoricalRead.write_uncleaned_parquet(combined, parquet_path)

    lighter_pool_count = lighter_df["address"].nunique()
    logger.info(
        "Merged %d Lighter pools (%d rows) into uncleaned %s",
        lighter_pool_count,
        len(lighter_df),
        parquet_path,
    )

    return combined
