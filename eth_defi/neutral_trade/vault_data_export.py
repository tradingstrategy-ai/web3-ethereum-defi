"""Export admitted Solana USDC observations into the existing native pipeline.

EVM records remain private source observations. The canonical PPS is not an
individual investor's net-of-fees return. Native fees are externalised; shared
net-return analytics estimate the published base schedule, not wallet history.
"""

import datetime
import json
import logging
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pandas as pd

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.neutral_trade.api import parse_vault
from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID, make_solana_vault_address
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase
from eth_defi.neutral_trade.tags import get_strategy_tags
from eth_defi.neutral_trade.vault_metadata import NATIVE_VAULT_METADATA
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.curator import get_curator_name
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.fee import FeeData, VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.risk import get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

# ruff: noqa: DTZ001, DTZ901 - dataset timestamps are naive UTC
logger = logging.getLogger(__name__)
PPS_RETURN_REVIEW_LIMIT = 0.5
#: Net analytics use current base rates, without reconstructing personal fees.
FEE_BASIS = "Published base-schedule estimate; wallet discounts and personal high-water-mark history are not reconstructed"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

#: Manually inspected canonical basis boundary, 2026-11-12: OracleUpdated
#: observations changed share denomination on 2026-07-09 while equity stayed
#: near 79k USDC. Earlier raw PPS is retained privately, never rescaled.
CANONICAL_STARTS = {"3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe": datetime.datetime(2026, 7, 9)}


def is_publishable(record: dict) -> bool:
    """Admit verified native USDC configuration without claiming net returns.

    Unknown denominations and explicit visibility changes fail closed. No EVM
    record is exported, irrespective of which source ran first.

    :param record: Retained source lead with last-seen timestamp.
    :return: Whether metadata and internal raw history can enter shared storage.
    """
    raw = record["raw"]
    return record["chain"] == "solana" and not record["skip_reason"] and raw.get("visible") is True and raw.get("private") is False and raw.get("asset", {}).get("mint") == USDC_MINT


def resolve_native_fees(fees: FeeData) -> FeeData:
    """Classify native Solana user-level charges without inventing missing rates.

    The `fee schedule <https://docs.neutral.trade/additional-info/fees-+-redemption-period>`_
    and `SDK accounting <https://github.com/neutral-trade/sdk/blob/7d8b41044e757c378d9a20eddfafa507ee2ec15c/src/extensions/fees.ts>`_
    describe deductions from individual shares, rather than a fees-net common
    share price. Only an explicitly all-zero schedule (such as NT Earn) is
    feeless. Missing rates still prevent net-return calculations. This resolver
    must not be used for EVM products with different vault contracts.

    :param fees: Published native base rates; unknown values remain ``None``.
    :return: New fee record preserving rates and classifying their mechanism.
    """
    mode = VaultFeeMode.feeless if all(rate == 0 for rate in (fees.management, fees.performance, fees.deposit, fees.withdraw)) else get_vault_fee_mode("Neutral Trade", "")
    return replace(fees, fee_mode=mode)


def apply_reviewed_metadata(row: VaultRow, address: str) -> bool:
    """Supplement REST records with reviewed, address-scoped public metadata.

    The `REST contract <https://api.neutral.trade/openapi.json>`_ exposes a
    manager wallet, not an organisation or strategy description. Official
    strategy pages establish the curated overlay; unknown accounts receive
    no inferred curator or generic protocol description. The source URL is
    retained separately from provider-reported metrics.

    :param row: Native vault metadata to enrich in place.
    :param address: Exact case-sensitive base58 account from the directory.
    :return: Whether a reviewed overlay was available and applied.
    """
    metadata = NATIVE_VAULT_METADATA.get(address)
    if metadata is None:
        return False
    row["_curator_slug"] = metadata.curator_slug
    row["_manager_name"] = get_curator_name(metadata.curator_slug)
    assert row["_manager_name"], f"Missing curator profile: {metadata.curator_slug}"
    row["_description"] = normalise_vault_description(metadata.description)
    row["_short_description"] = normalise_vault_short_description(metadata.short_description)
    row["_neutral_trade"]["metadata_source"] = metadata.source_url
    return True


def create_vault_row(record: dict) -> tuple[VaultSpec, VaultRow]:
    """Construct the native metadata shape already used by Derive and ApeX.

    API base rates and the documented externalised mechanism feed the shared
    net-return estimator. It does not reproduce personal settlement history.

    :param record: Verified public native USDC source lead.
    :return: Stable Solana dataset identity and shared metadata row.
    """
    assert is_publishable(record)
    vault = parse_vault(record["raw"])
    raw = vault.raw
    address = make_solana_vault_address(vault.address)
    metrics = (record["metrics"] or {}).get("data") or {}
    asset_nav = metrics.get("tvl", {}).get("value", {}).get("token")
    fees = {name: raw.get(field) / 10_000 if raw.get(field) is not None else None for name, field in (("management", "managementFeeBps"), ("performance", "performanceFeeBps"), ("deposit", "depositFeeBps"), ("withdraw", "withdrawalFeeBps"))}
    first_seen = max(min(record["first_seen"], record.get("first_price") or record["first_seen"]), CANONICAL_STARTS.get(vault.address, datetime.datetime.min))
    detection = ERC4262VaultDetection(chain=SOLANA_DATASET_CHAIN_ID, address=address, first_seen_at_block=0, first_seen_at=first_seen, features={ERC4626Feature.neutral_trade_native}, updated_at=record["last_seen"], deposit_count=0, redeem_count=0)
    extension = {"vault_address": vault.address, "source_chain": "solana", "explorer_url": "https://solscan.io/account/" + vault.address, "category": raw.get("category"), "manager": raw.get("manager"), "product_tier": None, "visible": True, "net_returns_verified": False, "observed_at": record["last_seen"].isoformat(), "provider_metrics": metrics, "fee_basis": FEE_BASIS}
    row: VaultRow = {
        "Symbol": vault.name or vault.address,
        "Name": vault.name or vault.address,
        "Address": address,
        "Denomination": "USDC",
        "Share token": vault.name or vault.address,
        "NAV": Decimal(asset_nav) if asset_nav else None,
        "Shares": None,
        "Protocol": "Neutral Trade",
        "Link": "https://www.neutral.trade/strategies",
        "First seen": first_seen,
        "Mgmt fee": fees["management"],
        "Perf fee": fees["performance"],
        "Deposit fee": fees["deposit"],
        "Withdraw fee": fees["withdraw"],
        "Features": "",
        "_detection_data": detection,
        "_denomination_token": {"address": None, "symbol": "USDC", "decimals": 6},
        "_share_token": None,
        "_fees": resolve_native_fees(FeeData(fee_mode=None, **fees)),
        "_flags": set(),
        "_strategy_tags": get_strategy_tags(address),
        "_lockup": datetime.timedelta(seconds=raw["withdrawalDelay"]) if raw.get("withdrawalDelay") is not None else None,
        "_description": None,
        "_short_description": None,
        "_manager_name": None,
        "_notes": "This vault's valuations are provider-reported. Returns after each depositor's fees and the underlying trading positions have not been independently verified.",
        "_available_liquidity": None,
        "_utilisation": None,
        "_deposit_closed_reason": "Provider has disabled deposits" if raw.get("paused") is True or raw.get("enabled") is False or raw.get("deprecated") is True else None,
        "_deposit_next_open": None,
        "_redemption_closed_reason": None,
        "_redemption_next_open": None,
        "_deposit_permission": VaultDepositPermission.unknown.value,
        "_whitelist_notes": None,
        "_share_price_source": PriceSource.api,
        "_neutral_trade": extension,
    }
    apply_reviewed_metadata(row, vault.address)
    return VaultSpec(SOLANA_DATASET_CHAIN_ID, address), row


def merge_into_vault_database(db: NeutralTradeDatabase, path: Path, *, vault_ids: set[str] | None = None) -> VaultDatabase:
    """Merge approved native metadata, keeping EVM identities unchanged.

    Revoked directory approval marks retained archival metadata ineligible for
    public cleaning/JSON. Neither archival metadata nor raw history is purged.

    :param db: Locked private source archive.
    :param path: Shared metadata path under the caller's pipeline writer lock.
    :param vault_ids: Optional fixed source inventory for numbered migrations.
    :return: Merged shared database.
    """
    vault_db = VaultDatabase.read(path) if path.exists() else VaultDatabase()
    prices = build_raw_prices_dataframe(db, vault_ids=vault_ids)
    verified_addresses = set(prices["address"]) if not prices.empty else set()
    quarantined = set(prices.attrs.get("quarantined_addresses", []))
    for record in db.iter_leads():
        if vault_ids is not None and record["address"] not in vault_ids:
            continue
        if record["chain"] != "solana":
            continue
        spec = VaultSpec(SOLANA_DATASET_CHAIN_ID, make_solana_vault_address(record["address"]))
        if not is_publishable(record):
            previous = vault_db.rows.get(spec)
            if previous is not None:
                previous["_neutral_trade"]["visible"] = False
            continue
        spec, row = create_vault_row(record)
        previous = vault_db.rows.get(spec)
        previous_risk = previous.get("_risk") if previous else None
        default_risk = get_vault_risk("Neutral Trade", spec.vault_address)
        if previous_risk is not None and (default_risk is None or previous_risk.value > default_risk.value):
            row["_risk"] = previous_risk
        row["_neutral_trade"]["history_verified"] = spec.vault_address not in quarantined and (spec.vault_address in verified_addresses or bool(previous and previous.get("_neutral_trade", {}).get("history_verified")))
        if previous and previous["First seen"] < row["First seen"]:
            row["First seen"] = max(previous["First seen"], CANONICAL_STARTS.get(record["address"], datetime.datetime.min))
            row["_detection_data"] = replace(row["_detection_data"], first_seen_at=row["First seen"])
        vault_db.rows[spec] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(path)
    return vault_db


def build_raw_prices_dataframe(db: NeutralTradeDatabase, *, vault_ids: set[str] | None = None) -> pd.DataFrame:
    """Build exact-timestamp USDC prices from canonical non-legacy history.

    Daily final closes fill dates with no canonical tick, never legacy means.
    USD source values are not placed in denomination-based total_assets.

    :param db: Locked private source archive.
    :param vault_ids: Optional fixed source inventory for a numbered migration.
    :return: RawVaultPriceRow-shaped frame with float USDC PPS/NAV/supply,
        uint32 chain, uint64 zero block number and naive UTC timestamps.
    """
    result = []
    ambiguous = set()
    for record in db.iter_leads():
        if vault_ids is not None and record["address"] not in vault_ids:
            continue
        if not is_publishable(record):
            continue
        observations = db.con.execute("SELECT endpoint,timestamp,raw,written_at FROM observations WHERE chain=? AND address=? ORDER BY timestamp,observation_key", [record["chain"], record["address"]]).fetchall()
        tick_dates = {timestamp.date() for endpoint, timestamp, raw, _ in observations if endpoint == "share-price" and json.loads(raw).get("isLegacy") is False}
        seen = {}
        for endpoint, timestamp, encoded, written in observations:
            raw = json.loads(encoded)
            if timestamp < CANONICAL_STARTS.get(record["address"], datetime.datetime.min):
                continue
            if raw.get("isLegacy") is not False:
                continue
            if endpoint == "snapshots":
                if raw.get("method") != "close" or timestamp.date() in tick_dates:
                    continue
                pps, equity, shares = raw["closePps"], raw["closeTvlToken"], raw["closeTotalShares"]
            else:
                pps, equity, shares = raw["pps"], raw["totalEquity"], raw["totalShares"]
            # Two distinct canonical source events with different values in the
            # same second cannot be ordered from the public response contract.
            values = (pps, equity, shares)
            if timestamp in seen and seen[timestamp] != values:
                ambiguous.add(make_solana_vault_address(record["address"]))
                continue
            seen[timestamp] = values
            scale = Decimal(10) ** record["raw"]["asset"]["decimals"]
            numbers = [Decimal(value) / scale for value in values]
            assert all(value.is_finite() and value >= 0 for value in numbers) and numbers[0] > 0
            result.append({"chain": SOLANA_DATASET_CHAIN_ID, "address": make_solana_vault_address(record["address"]), "block_number": 0, "timestamp": timestamp, "share_price": float(numbers[0]), "total_assets": float(numbers[1]), "total_supply": float(numbers[2]), "management_fee": None, "performance_fee": None, "errors": "", "written_at": written})
    if not result:
        return pd.DataFrame()
    frame = pd.DataFrame(result).drop_duplicates(["chain", "address", "timestamp"], keep="last")
    frame = frame.sort_values(["address", "timestamp"])
    jumps = frame.groupby("address")["share_price"].pct_change().abs() > PPS_RETURN_REVIEW_LIMIT
    if jumps.any() or ambiguous:
        # Never repair a large loss or redenomination by assuming it away.
        # Quarantine the whole unknown basis until its source interval is reviewed.
        affected = set(frame.loc[jumps, "address"]) | ambiguous
        logger.error("Withholding %s native vault histories pending PPS continuity review", len(affected))
        frame = frame[~frame["address"].isin(affected)]
        frame.attrs["quarantined_addresses"] = sorted(affected)
    frame["chain"] = frame["chain"].astype("uint32")
    frame["block_number"] = frame["block_number"].astype("uint64")
    return frame
