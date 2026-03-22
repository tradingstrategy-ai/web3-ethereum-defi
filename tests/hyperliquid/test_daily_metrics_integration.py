"""Integration test: unified vault metrics JSON from Hyperliquid + ERC-4626 data.

Scans a single Hyperliquid vault and creates a synthetic Arbitrum ERC-4626 vault,
merges both into shared pipeline files, runs the full analysis pipeline, and
verifies the combined JSON output contains metrics from both vaults.

Requires network access to the Hyperliquid API.
"""

import datetime
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eth_defi.erc_4626.core import ERC4626Feature, ERC4262VaultDetection
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.daily_metrics import (
    HyperliquidDailyMetricsDatabase,
    fetch_and_store_vault,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import HyperliquidVault, VaultSummary, fetch_all_vaults
from eth_defi.hyperliquid.vault_data_export import (
    LEADER_FRACTION_WARNING_THRESHOLD,
    _get_deposit_closed_reason,
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    export_lifetime_row,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow


def _create_mock_arbitrum_vault_data(
    vault_db_path: Path,
    uncleaned_path: Path,
) -> tuple[VaultDatabase, pd.DataFrame]:
    """Create a synthetic Arbitrum ERC-4626 vault with 90 days of raw price data.

    Produces raw format matching the EVM vault scanner output, so it
    can go through the standard cleaning pipeline together with
    Hypercore data.
    """

    chain_id = 42161
    vault_address = "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=vault_address,
        first_seen_at_block=100000000,
        first_seen_at=datetime.datetime(2024, 6, 1),
        features={ERC4626Feature.d2_like},
        updated_at=datetime.datetime(2025, 12, 28),
        deposit_count=50,
        redeem_count=10,
    )

    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_skimming,
        management=0.02,
        performance=0.20,
        deposit=0.0,
        withdraw=0.0,
    )

    vault_row: VaultRow = {
        "Symbol": "HYPE++",
        "Name": "D2 Hype++",
        "Address": vault_address,
        "Denomination": "USDC",
        "Share token": "HYPE++",
        "NAV": Decimal("500000"),
        "Peak NAV": Decimal("600000"),
        "Shares": Decimal("450000"),
        "Protocol": "D2 Finance",
        "Link": f"https://arbiscan.io/address/{vault_address}",
        "First seen": datetime.datetime(2024, 6, 1),
        "Mgmt fee": 0.02,
        "Perf fee": 0.20,
        "Deposit fee": 0.0,
        "Withdrawal fee": 0.0,
        "_detection_data": detection,
        "_denomination_token": {"address": "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "symbol": "USDC", "decimals": 6},
        "_share_token": {"address": vault_address, "symbol": "HYPE++", "decimals": 18},
        "_fees": fee_data,
        "_flags": set(),
        "_lockup": None,
        "_description": "Test Arbitrum vault",
        "_short_description": "Test Arbitrum vault",
        "features": {ERC4626Feature.d2_like},
    }

    spec = VaultSpec(chain_id=chain_id, vault_address=vault_address)
    vault_db = VaultDatabase(rows={spec: vault_row})
    vault_db.write(vault_db_path)

    # Create 90 days of synthetic price data with a gentle uptrend (raw format)
    dates = pd.date_range(end="2025-12-28", periods=90, freq="D")
    np.random.seed(42)
    base_price = 1.0
    daily_returns = np.random.normal(0.0003, 0.005, len(dates))
    share_prices = base_price * np.cumprod(1 + daily_returns)

    prices_df = pd.DataFrame(
        {
            "chain": chain_id,
            "address": vault_address,
            "block_number": range(100000000, 100000000 + len(dates)),
            "timestamp": dates,
            "share_price": share_prices,
            "total_assets": share_prices * 450000,
            "total_supply": 450000.0,
            "performance_fee": 0.20,
            "management_fee": 0.02,
            "errors": "",
        },
    )

    prices_df["chain"] = prices_df["chain"].astype("int32")
    prices_df["block_number"] = prices_df["block_number"].astype("int64")

    prices_df.to_parquet(uncleaned_path, compression="zstd")

    return vault_db, prices_df


@pytest.mark.timeout(120)
def test_unified_vault_metrics_json(tmp_path):
    """Scan one Hypercore vault + one Arbitrum vault, merge, run analysis, verify combined JSON."""

    duckdb_path = tmp_path / "daily-metrics.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    uncleaned_path = tmp_path / "vault-prices-1h.parquet"
    cleaned_path = tmp_path / "cleaned-vault-prices-1h.parquet"
    output_json = tmp_path / "vault-metrics.json"

    # Step 1: Create synthetic Arbitrum vault data (raw format)
    _create_mock_arbitrum_vault_data(vault_db_path, uncleaned_path)

    # Step 2: Scan a single Hyperliquid vault
    session = create_hyperliquid_session()
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    # Find this vault in the bulk listing to get its VaultSummary
    all_vaults = list(fetch_all_vaults(session))
    target_summary = None
    for s in all_vaults:
        if s.vault_address.lower() == vault_address.lower():
            target_summary = s
            break

    assert target_summary is not None, f"Vault {vault_address} not found in bulk listing"

    db = HyperliquidDailyMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_vault(session, db, target_summary)
        assert result, "Failed to fetch and store Hyperliquid vault"
        db.save()

        assert db.get_vault_count() == 1
        assert db.get_vault_daily_price_count(vault_address) > 0

        # Verify written_at is filled in DuckDB
        daily_df = db.get_vault_daily_prices(vault_address)
        assert "written_at" in daily_df.columns, "written_at column missing from DuckDB daily prices"
        assert daily_df["written_at"].notna().all(), "written_at should be filled for all newly inserted rows"

        # Step 3: Merge Hyperliquid data into existing pipeline files
        merge_into_vault_database(db, vault_db_path)
        merge_into_uncleaned_parquet(db, uncleaned_path)
    finally:
        db.close()

    # Step 4: Run the cleaning pipeline (processes both EVM + Hypercore data)
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=uncleaned_path,
        cleaned_price_df_path=cleaned_path,
    )

    # Step 5: Run the full analysis pipeline
    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(cleaned_path)

    if not isinstance(prices_df.index, pd.DatetimeIndex):
        if "timestamp" in prices_df.columns:
            prices_df = prices_df.set_index("timestamp")

    assert len(vault_db) >= 2, f"Expected at least 2 vaults, got {len(vault_db)}"

    chains = prices_df["chain"].unique()
    assert HYPERCORE_CHAIN_ID in chains, f"Hypercore chain not in price data, got chains: {chains}"
    assert 42161 in chains, f"Arbitrum chain not in price data, got chains: {chains}"

    # Verify written_at survives the cleaning pipeline
    assert "written_at" in prices_df.columns, "written_at column missing from cleaned data"
    # Hyperliquid rows should have written_at filled; synthetic Arbitrum rows will have NaT
    hl_prices = prices_df[prices_df["chain"] == HYPERCORE_CHAIN_ID]
    assert hl_prices["written_at"].notna().all(), "Hyperliquid rows should have written_at filled after cleaning"

    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    lifetime_data_df = calculate_lifetime_metrics(returns_df, vault_db)

    assert len(lifetime_data_df) >= 2, f"Expected at least 2 vault records, got {len(lifetime_data_df)}"

    # Step 5: Export to JSON
    vaults = [export_lifetime_row(r) for _, r in lifetime_data_df.iterrows()]

    output_data = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vaults": vaults,
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, allow_nan=False)

    # Step 6: Verify the JSON
    with open(output_json) as f:
        data = json.load(f)

    assert "vaults" in data
    assert len(data["vaults"]) >= 2

    # Find the vaults by chain
    hl_vaults = [v for v in data["vaults"] if v.get("chain_id") == HYPERCORE_CHAIN_ID]
    arb_vaults = [v for v in data["vaults"] if v.get("chain_id") == 42161]

    assert len(hl_vaults) >= 1, f"No Hypercore vault in output. Vaults: {[v.get('chain_id') for v in data['vaults']]}"
    assert len(arb_vaults) >= 1, f"No Arbitrum vault in output. Vaults: {[v.get('chain_id') for v in data['vaults']]}"

    # Verify Hyperliquid vault
    hl_vault = hl_vaults[0]
    assert hl_vault["protocol"] == "Hyperliquid"
    assert hl_vault["denomination"] == "USDC"

    # Verify Arbitrum vault
    arb_vault = arb_vaults[0]
    assert arb_vault["protocol"] == "D2 Finance"
    assert arb_vault["chain_id"] == 42161

    # Both should have period results
    for vault in [hl_vault, arb_vault]:
        assert "period_results" in vault, f"Missing period_results for vault {vault.get('name')}"
        periods = vault["period_results"]
        assert len(periods) > 0, f"Empty period_results for vault {vault.get('name')}"

        # Lifetime period should always exist
        lifetime = [p for p in periods if p["period"] == "lifetime"]
        assert len(lifetime) == 1, f"Missing lifetime period for vault {vault.get('name')}"

    # Hyperliquid vault should have netflow metrics with 1d, 7d, 30d periods
    assert "netflow" in hl_vault, f"Missing netflow field for Hyperliquid vault"
    hl_netflow = hl_vault["netflow"]
    assert hl_netflow is not None, "Hyperliquid vault should have netflow data"
    assert isinstance(hl_netflow, list), f"Expected netflow to be a list, got {type(hl_netflow)}"
    assert len(hl_netflow) == 3, f"Expected 3 netflow periods (1d, 7d, 30d), got {len(hl_netflow)}"

    netflow_periods = {nf["period"] for nf in hl_netflow}
    assert netflow_periods == {"1d", "7d", "30d"}, f"Unexpected netflow periods: {netflow_periods}"

    for nf in hl_netflow:
        assert "deposit_count" in nf
        assert "withdrawal_count" in nf
        assert "deposit_usd" in nf
        assert "withdrawal_usd" in nf
        assert "net_flow_usd" in nf
        assert nf["deposit_count"] >= 0
        assert nf["withdrawal_count"] >= 0

    # Arbitrum vault has no flow data — netflow should be null
    assert arb_vault.get("netflow") is None, f"Arbitrum vault should have null netflow, got: {arb_vault.get('netflow')}"


@pytest.mark.timeout(120)
def test_deposit_closed_vault_pipeline(tmp_path):
    """Verify deposit_closed_reason propagates through the full pipeline for a vault with allow_deposits=False.

    Uses vault "[A] Downside" (0x4af52283ea6de9236c47b28e5dbf156453df8efb)
    which has is_closed=False but allow_deposits=False on Hyperliquid.
    """

    vault_address = "0x4af52283ea6de9236c47b28e5dbf156453df8efb"

    duckdb_path = tmp_path / "daily-metrics.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    uncleaned_path = tmp_path / "vault-prices-1h.parquet"
    cleaned_path = tmp_path / "cleaned-vault-prices-1h.parquet"

    # Construct a minimal VaultSummary directly — avoids fetching all 8000+ vaults.
    # fetch_and_store_vault() only uses vault_address, create_time, tvl, and apr
    # from the summary; the rest comes from the vaultDetails API call.
    target_summary = VaultSummary(
        name="[A] Downside",
        vault_address=vault_address,
        leader="0x0000000000000000000000000000000000000000",
        tvl=Decimal("0"),
        is_closed=False,
        relationship_type="normal",
    )

    session = create_hyperliquid_session()

    # Step 1: Fetch vault data and store in DuckDB
    db = HyperliquidDailyMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_vault(session, db, target_summary)
        assert result, f"Failed to fetch vault {vault_address}"
        db.save()

        # Step 2: Verify DuckDB metadata has allow_deposits=False
        metadata_df = db.get_all_vault_metadata()
        vault_meta = metadata_df[metadata_df["vault_address"] == vault_address.lower()]
        assert len(vault_meta) == 1
        assert vault_meta.iloc[0]["allow_deposits"] == False, "Expected allow_deposits=False from Hyperliquid API"
        assert vault_meta.iloc[0]["is_closed"] == False, "Expected is_closed=False"

        # Step 2b: Verify daily prices track deposit status on the latest row only
        prices_df_raw = db.get_vault_daily_prices(vault_address)
        assert len(prices_df_raw) > 1, "Expected multiple daily price rows"

        # Latest row should have deposit status from the API
        latest_row = prices_df_raw.iloc[-1]
        assert latest_row["is_closed"] == False, "Latest row should have is_closed=False"
        assert latest_row["allow_deposits"] == False, "Latest row should have allow_deposits=False"

        # Historical rows should have NULL deposit status
        historical_rows = prices_df_raw.iloc[:-1]
        assert historical_rows["is_closed"].isna().all(), "Historical rows should have is_closed=NULL"
        assert historical_rows["allow_deposits"].isna().all(), "Historical rows should have allow_deposits=NULL"

        # Leader metrics — latest row should have values, historical rows NULL
        assert latest_row["leader_fraction"] is not None, "Latest row should have leader_fraction"
        assert 0 < latest_row["leader_fraction"] <= 1.0, f"leader_fraction should be between 0 and 1, got {latest_row['leader_fraction']}"
        assert historical_rows["leader_fraction"].isna().all(), "Historical rows should have leader_fraction=NULL"

        assert latest_row["leader_commission"] is not None, "Latest row should have leader_commission"
        assert historical_rows["leader_commission"].isna().all(), "Historical rows should have leader_commission=NULL"

        # Leader fraction history should have exactly 1 row (single scan of a fresh DB)
        lf_history = db.get_leader_fraction_history(vault_address)
        assert len(lf_history) == 1, f"Expected exactly 1 leader_fraction snapshot, got {len(lf_history)}"
        assert 0 < lf_history.iloc[0]["leader_fraction"] <= 1.0

        # Verify flow columns are present in DuckDB after scan
        assert "daily_deposit_count" in prices_df_raw.columns, "daily_deposit_count column missing"
        assert "daily_withdrawal_count" in prices_df_raw.columns, "daily_withdrawal_count column missing"
        assert "daily_deposit_usd" in prices_df_raw.columns, "daily_deposit_usd column missing"
        assert "daily_withdrawal_usd" in prices_df_raw.columns, "daily_withdrawal_usd column missing"

        # Verify flow_data_earliest_date is tracked in metadata
        metadata_df = db.get_all_vault_metadata()
        vault_meta = metadata_df[metadata_df["vault_address"] == vault_address.lower()]
        flow_earliest = vault_meta.iloc[0].get("flow_data_earliest_date")
        assert flow_earliest is not None, "flow_data_earliest_date should be set after scan"

        # Step 3: Merge into VaultDatabase and verify _deposit_closed_reason
        merge_into_vault_database(db, vault_db_path)
        merge_into_uncleaned_parquet(db, uncleaned_path)
    finally:
        db.close()

    vault_db = VaultDatabase.read(vault_db_path)
    spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=vault_address.lower())
    assert spec in vault_db.rows, f"Vault {vault_address} not in VaultDatabase"
    vault_row = vault_db.rows[spec]
    assert vault_row["_deposit_closed_reason"] == "Vault deposits disabled by leader", f"Expected specific reason for allow_deposits=False, got: {vault_row['_deposit_closed_reason']}"

    # Step 4: Run cleaning pipeline
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=uncleaned_path,
        cleaned_price_df_path=cleaned_path,
    )

    # Step 5: Run analysis pipeline
    prices_df = pd.read_parquet(cleaned_path)
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        if "timestamp" in prices_df.columns:
            prices_df = prices_df.set_index("timestamp")

    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    lifetime_data_df = calculate_lifetime_metrics(returns_df, vault_db)

    assert len(lifetime_data_df) >= 1, f"Expected at least 1 vault record, got {len(lifetime_data_df)}"

    # Step 6: Verify deposit_closed_reason in lifetime metrics
    assert len(lifetime_data_df) == 1
    vault_record = lifetime_data_df.iloc[0]
    assert vault_record["deposit_closed_reason"] == "Vault deposits disabled by leader", f"Expected specific reason in lifetime metrics, got: {vault_record['deposit_closed_reason']}"

    # Step 7: Verify leader metrics flow through to lifetime data
    assert vault_record["leader_fraction"] is not None, "leader_fraction should be in lifetime metrics"
    assert 0 < vault_record["leader_fraction"] <= 1.0, f"leader_fraction out of range: {vault_record['leader_fraction']}"
    assert vault_record["leader_commission"] is not None, "leader_commission should be in lifetime metrics"

    # Step 8: Verify in JSON export
    exported = export_lifetime_row(vault_record)
    assert exported["deposit_closed_reason"] == "Vault deposits disabled by leader", f"Expected specific reason in JSON export, got: {exported['deposit_closed_reason']}"
    assert exported["leader_fraction"] is not None, "leader_fraction should be in JSON export"
    assert exported["leader_commission"] is not None, "leader_commission should be in JSON export"


def test_deposit_closed_reason_leader_fraction():
    """Verify _get_deposit_closed_reason returns correct reasons based on leader_fraction threshold."""

    # Open vault with healthy leader fraction — no reason
    assert _get_deposit_closed_reason(is_closed=False, allow_deposits=True, leader_fraction=0.20) is None

    # Open vault with no leader_fraction data — no reason
    assert _get_deposit_closed_reason(is_closed=False, allow_deposits=True, leader_fraction=None) is None

    # Leader fraction just above threshold — no reason
    assert _get_deposit_closed_reason(is_closed=False, allow_deposits=True, leader_fraction=LEADER_FRACTION_WARNING_THRESHOLD + 0.001) is None

    # Leader fraction below threshold — warning
    reason = _get_deposit_closed_reason(is_closed=False, allow_deposits=True, leader_fraction=0.050)
    assert reason is not None
    assert "Leader share" in reason

    # Closed vault takes priority over leader_fraction
    reason = _get_deposit_closed_reason(is_closed=True, allow_deposits=True, leader_fraction=0.03)
    assert reason == "Vault is permanently closed"

    # Deposits disabled takes priority over leader_fraction
    reason = _get_deposit_closed_reason(is_closed=False, allow_deposits=False, leader_fraction=0.03)
    assert reason == "Vault deposits disabled by leader"
