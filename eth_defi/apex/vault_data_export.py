"""Export ApeX vault data into the shared vault pipeline format.

This module bridges the ApeX-specific DuckDB data into the metadata pickle
and raw price dataframe consumed by the multi-chain vault pipeline. ApeX
timestamps are preserved exactly; the exporter does not resample or bucket
the source observations.
"""

import datetime
import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def create_apex_vault_row(
    vault_id: str,
    *,
    name: str,
    description: str | None,
    tvl: float | None,
    share_count: float | None,
    created_at: datetime.datetime | None,
    first_seen: datetime.datetime,
    status: str,
) -> tuple[VaultSpec, VaultRow]:
    """Create one synthetic shared-pipeline row for an ApeX native vault.

    The ApeX platform vault ID remains the identity through the
    ``apex-vault-{vault_id}`` address. Fee source values are deliberately not
    interpreted because ApeX does not authoritatively document their units.

    :param vault_id:
        Stable ApeX platform vault ID.
    :param name:
        Vault display name.
    :param description:
        Vault strategy description.
    :param tvl:
        Current total value in ApeX USDT terms.
    :param share_count:
        Current source-reported share count.
    :param created_at:
        Source creation timestamp as naive UTC.
    :param first_seen:
        Reader first-observation timestamp as naive UTC.
    :param status:
        Raw ApeX lifecycle status.
    :return:
        Synthetic vault specification and metadata row.
    """
    address = f"apex-vault-{vault_id}"
    detection = ERC4262VaultDetection(
        chain=APEX_CHAIN_ID,
        address=address,
        first_seen_at_block=0,
        first_seen_at=created_at or first_seen,
        features={ERC4626Feature.apex_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )
    fees = FeeData(
        fee_mode=None,
        management=None,
        performance=None,
        deposit=None,
        withdraw=None,
    )
    row: VaultRow = {
        "Symbol": name[:10],
        "Name": name,
        "Address": address,
        "Denomination": "USDT",
        "Share token": name[:10],
        "NAV": Decimal(str(tvl or 0.0)),
        "Shares": Decimal(str(share_count or 0.0)),
        "Protocol": "ApeX",
        "Link": "https://omni.apex.exchange/",
        "First seen": created_at or first_seen,
        "Mgmt fee": None,
        "Perf fee": None,
        "Deposit fee": None,
        "Withdraw fee": None,
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {
            "address": "0x0000000000000000000000000000000000000000",
            "symbol": "USDT",
            "decimals": 6,
        },
        "_share_token": None,
        "_fees": fees,
        "_flags": {VaultFlag.perp_dex_trading_vault},
        "_lockup": None,
        "_description": description,
        "_short_description": None,
        "_manager_name": None,
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": "Vault is permanently closed" if status == "VAULT_FINISHED" else None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
    }
    return VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=address), row


def build_raw_prices_dataframe(db: ApexMetricsDatabase) -> pd.DataFrame:
    """Build raw shared-pipeline price rows from the ApeX DuckDB.

    Every actual ranking or history timestamp is retained. The resulting
    columns match :class:`eth_defi.vault.base.RawVaultPriceRow`; ApeX has no
    native block number, so ``block_number`` is zero.

    :param db:
        Open owner-thread ApeX metrics database.
    :return:
        Raw price dataframe, or an empty dataframe when the database has no
        observations.
    """
    prices = db.get_vault_prices()
    if prices.empty:
        return pd.DataFrame()

    result = pd.DataFrame(
        {
            "chain": APEX_CHAIN_ID,
            "address": prices["synthetic_address"].values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices["timestamp"]).values,
            "share_price": prices["share_price"].values,
            "total_assets": prices["total_assets"].values,
            "total_supply": prices["total_supply"].values,
            "performance_fee": None,
            "management_fee": None,
            "errors": "",
            "written_at": pd.to_datetime(prices["written_at"]).values,
        }
    )
    result["chain"] = result["chain"].astype("uint32")
    result["block_number"] = result["block_number"].astype("uint64")
    return result


def merge_into_vault_database(
    db: ApexMetricsDatabase,
    vault_db_path: Path,
) -> VaultDatabase:
    """Merge ApeX metadata into the shared vault database pickle.

    Existing ApeX identities are replaced in place while unrelated protocol
    rows are preserved. Missing ApeX ranking entries remain present because
    the reader retains their metadata and lifecycle state.

    :param db:
        Open owner-thread ApeX metrics database.
    :param vault_db_path:
        Shared vault metadata pickle path.
    :return:
        Updated shared vault database.
    """
    if vault_db_path.exists():
        vault_db = VaultDatabase.read(vault_db_path)
    else:
        vault_db_path.parent.mkdir(parents=True, exist_ok=True)
        vault_db = VaultDatabase()

    metadata = db.get_vault_metadata()
    added = 0
    updated = 0
    for record in metadata.to_dict(orient="records"):
        tvl = record["current_tvl"]
        share_count = record["current_share_count"]
        created_at = record["created_at"]
        description = record["description"]
        spec, vault_row = create_apex_vault_row(
            vault_id=str(record["vault_id"]),
            name=str(record["name"] or ""),
            description=None if pd.isna(description) else str(description),
            tvl=None if pd.isna(tvl) else float(tvl),
            share_count=None if pd.isna(share_count) else float(share_count),
            created_at=None if pd.isna(created_at) else created_at,
            first_seen=record["first_seen"],
            status=str(record["status"]),
        )
        if spec in vault_db.rows:
            updated += 1
        else:
            added += 1
        vault_db.rows[spec] = vault_row

    vault_db.write(vault_db_path)
    logger.info(
        "Merged %d ApeX vaults into %s (%d new, %d updated)",
        added + updated,
        vault_db_path,
        added,
        updated,
    )
    return vault_db
