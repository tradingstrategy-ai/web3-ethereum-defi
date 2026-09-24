"""Export Derive v3 native vaults to the shared vault dataset.

Only mainnet rows are eligible for the shared catalogue. Derive reports USD
accounting values even when a vault accepts another deposit asset; the latter
is kept as a separate source field in the metadata export.

See `Derive's public vault API
<https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vaults>`__.
"""

import dataclasses
import datetime
import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.derive.tags import get_strategy_tags
from eth_defi.derive.v3_constants import DERIVE_V3_CHAIN_ID, make_derive_v3_vault_address
from eth_defi.derive.v3_vault_metrics import DeriveV3VaultDatabase
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def _decimal_or_none(value: object) -> Decimal | None:
    """Convert a nullable DuckDB/Pandas value to a precise decimal.

    :param value: Source decimal string or null-like value.
    :return: Decimal value or ``None``.
    """
    return None if value is None or pd.isna(value) else Decimal(str(value))


def _value_or_none(value: object) -> object | None:
    """Replace Pandas null markers with Python ``None``.

    :param value: Value read from the metadata dataframe.
    :return: Original value or ``None`` for Pandas nulls.
    """
    return None if value is None or pd.isna(value) else value


def create_derive_v3_vault_row(record: dict) -> tuple[VaultSpec, VaultRow]:  # noqa: PLR0914 - source fields remain explicit in the shared row
    """Create one synthetic mainnet vault metadata row.

    The USD denomination describes NAV and historical share prices. The
    accepted deposit token is separately retained in ``_derive_deposit_asset``.
    Completed fee settlements dilute shares and affect later sampled prices.
    The historical series does not simulate fees accrued since the last
    settlement.

    :param record: Mainnet ``vault_metadata`` DuckDB row.
    :return: Shared vault identity and metadata row.
    :raises ValueError: If the row is from testnet or lacks a deposit asset.
    """
    if record["network"] != "mainnet":
        message = "Only Derive v3 mainnet vaults can enter the shared dataset"
        raise ValueError(message)
    subaccount_id = int(record["subaccount_id"])
    address = make_derive_v3_vault_address(subaccount_id)
    deposit_address = str(record["deposit_spot_asset"] or "")
    if not deposit_address:
        raise ValueError(f"Derive v3 vault {subaccount_id} has no deposit asset")
    first_seen = _value_or_none(record.get("first_price_at")) or record["observed_at"]
    if isinstance(first_seen, pd.Timestamp):
        first_seen = first_seen.to_pydatetime()
    if first_seen is None or pd.isna(first_seen):
        first_seen = native_datetime_utc_now()
    tags = get_strategy_tags(address)
    flags = {VaultFlag.perp_dex_trading_vault} if tags is not None and StrategyTag.perpetual_futures in tags else set()
    whitelist_only = bool(record["whitelist_only"])
    permission = VaultDepositPermission.whitelisted if whitelist_only else VaultDepositPermission.permissionless
    whitelist_notes = "Derive curator restricts deposits to approved accounts." if whitelist_only else None
    deposit_closed_reason = "Derive vault is closed" if bool(record["closed"]) else None

    management_fee = int(record["management_fee_bps"]) / 10_000
    performance_fee = int(record["performance_fee_bps"]) / 10_000
    fees = FeeData(
        fee_mode=VaultFeeMode.internalised_minting,
        management=management_fee,
        performance=performance_fee,
        deposit=None,
        withdraw=None,
    )
    deposit_symbol = _value_or_none(record.get("deposit_symbol"))
    deposit_decimals = _value_or_none(record.get("deposit_decimals"))
    deposit_asset = {
        "address": deposit_address,
        "symbol": str(deposit_symbol) if deposit_symbol is not None else None,
        "decimals": int(deposit_decimals) if deposit_decimals is not None else None,
    }
    curator = str(record["curator"])
    name = str(record["name"] or f"Derive vault {subaccount_id}")
    description = str(record["description"] or "")
    detection = ERC4262VaultDetection(
        chain=DERIVE_V3_CHAIN_ID,
        address=address,
        first_seen_at_block=0,
        first_seen_at=first_seen,
        features={ERC4626Feature.derive_v3_native},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )
    row: VaultRow = {
        "Symbol": name[:10],
        "Name": name,
        "Address": address,
        "Denomination": "USD",
        "Share token": name[:10],
        "NAV": _decimal_or_none(record.get("nav_usd")),
        "Shares": Decimal(str(record["total_shares"])),
        "Protocol": "Derive",
        "Link": "https://app.derive.xyz/v3",
        "First seen": first_seen,
        "Mgmt fee": management_fee,
        "Perf fee": performance_fee,
        "Deposit fee": None,
        "Withdraw fee": None,
        "Features": "",
        "_detection_data": detection,
        # A synthetic USD unit gives the shared rate feeder a fixed 1:1 USD
        # conversion. The actual deposit token remains separate below.
        "_denomination_token": {"address": None, "symbol": "USD", "decimals": None},
        "_derive_deposit_asset": deposit_asset,
        "_derive_curator": curator,
        "_share_token": None,
        "_fees": fees,
        "_flags": flags,
        "_strategy_tags": tags,
        "_lockup": datetime.timedelta(seconds=int(record["cooldown_sec"])),
        "_description": description,
        "_short_description": None,
        "_manager_name": None,
        "_notes": f"Derive v3 curator {curator}; deposits and withdrawals settle through curator-managed requests.",
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": deposit_closed_reason,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        "_deposit_permission": permission.value,
        "_whitelist_notes": whitelist_notes,
        "_share_price_source": PriceSource.api,
    }
    return VaultSpec(chain_id=DERIVE_V3_CHAIN_ID, vault_address=address), row


def build_raw_prices_dataframe(db: DeriveV3VaultDatabase) -> pd.DataFrame:
    """Build mainnet raw price rows for the shared Parquet schema.

    Every timestamp is an actual Derive observation. USD share price, USD NAV
    and native share supply stay in the same units as the source API; there is
    no hourly interpolation or substitution of the live simulated price.

    :param db: Open Derive v3 DuckDB reader.
    :return: DataFrame with the columns in
        :class:`~eth_defi.vault.base.RawVaultPriceRow`: naive UTC timestamps,
        numeric USD ``share_price`` and ``total_assets`` (nullable NAV), and
        numeric ``total_supply`` in native shares. Empty when no mainnet
        history is stored. ``block_number`` is zero for these API observations.
    """
    prices = db.get_vault_prices("mainnet")
    if prices.empty:
        return pd.DataFrame()
    result = pd.DataFrame(
        {
            "chain": DERIVE_V3_CHAIN_ID,
            "address": prices["subaccount_id"].apply(lambda value: make_derive_v3_vault_address(int(value))).values,
            "block_number": 0,
            "timestamp": pd.to_datetime(prices["timestamp"]).values,
            "share_price": pd.to_numeric(prices["share_price"], errors="raise").values,
            "total_assets": pd.to_numeric(prices["nav_usd"], errors="coerce").values,
            "total_supply": pd.to_numeric(prices["total_shares"], errors="raise").values,
            "performance_fee": None,
            "management_fee": None,
            "errors": "",
            "written_at": pd.to_datetime(prices["written_at"]).values,
        }
    )
    result["chain"] = result["chain"].astype("uint32")
    result["block_number"] = result["block_number"].astype("uint64")
    return result


def merge_into_vault_database(db: DeriveV3VaultDatabase, vault_db_path: Path) -> VaultDatabase:
    """Upsert mainnet Derive metadata while retaining all existing vaults.

    If DuckDB has no mainnet metadata, the pickle is not written. Otherwise
    stored mainnet records are merged, including records retained after a
    vault disappears from the API listing. Existing unrelated vaults remain.

    :param db: Open Derive v3 DuckDB reader.
    :param vault_db_path: Shared vault metadata pickle path.
    :return: Updated or existing vault database.
    """
    metadata = db.get_vault_metadata("mainnet")
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    if metadata.empty:
        logger.info("No Derive v3 mainnet metadata to merge")
        return vault_db
    for record in metadata.to_dict(orient="records"):
        spec, row = create_derive_v3_vault_row(record)
        previous = vault_db.rows.get(spec)
        if previous is not None and previous["First seen"] < row["First seen"]:
            row["First seen"] = previous["First seen"]
            row["_detection_data"] = dataclasses.replace(row["_detection_data"], first_seen_at=previous["First seen"])
        vault_db.rows[spec] = row
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
    logger.info("Merged %d Derive v3 mainnet vaults into %s", len(metadata), vault_db_path)
    return vault_db
