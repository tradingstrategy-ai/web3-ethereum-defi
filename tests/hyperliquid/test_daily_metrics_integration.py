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
from eth_defi.hyperliquid.vault import HyperliquidVault, fetch_all_vaults
from eth_defi.hyperliquid.vault_data_export import (
    merge_into_cleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    export_lifetime_row,
)
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow


def _create_mock_arbitrum_vault_data(
    vault_db_path: Path,
    parquet_path: Path,
) -> tuple[VaultDatabase, pd.DataFrame]:
    """Create a synthetic Arbitrum ERC-4626 vault with 90 days of price data."""

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

    # Create 90 days of synthetic price data with a gentle uptrend
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
            "share_price": share_prices,
            "raw_share_price": share_prices,
            "total_assets": share_prices * 450000,
            "total_supply": 450000.0,
            "performance_fee": 0.20,
            "management_fee": 0.02,
            "errors": "",
            "id": f"{chain_id}-{vault_address}",
            "name": "D2 Hype++",
            "event_count": 60,
            "protocol": "D2 Finance",
            "returns_1h": np.concatenate([[0.0], daily_returns[1:]]),
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )

    prices_df["chain"] = prices_df["chain"].astype("int32")
    prices_df["block_number"] = prices_df["block_number"].astype("int64")

    prices_df.to_parquet(parquet_path, compression="zstd")

    return vault_db, prices_df


@pytest.mark.timeout(120)
def test_unified_vault_metrics_json(tmp_path):
    """Scan one Hypercore vault + one Arbitrum vault, merge, run analysis, verify combined JSON."""

    duckdb_path = tmp_path / "daily-metrics.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    parquet_path = tmp_path / "cleaned-vault-prices-1h.parquet"
    output_json = tmp_path / "vault-metrics.json"

    # Step 1: Create synthetic Arbitrum vault data
    _create_mock_arbitrum_vault_data(vault_db_path, parquet_path)

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

        # Step 3: Merge Hyperliquid data into existing files
        merge_into_vault_database(db, vault_db_path)
        merge_into_cleaned_parquet(db, parquet_path)
    finally:
        db.close()

    # Step 4: Run the full analysis pipeline
    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(parquet_path)

    if not isinstance(prices_df.index, pd.DatetimeIndex):
        if "timestamp" in prices_df.columns:
            prices_df = prices_df.set_index("timestamp")

    assert len(vault_db) >= 2, f"Expected at least 2 vaults, got {len(vault_db)}"

    chains = prices_df["chain"].unique()
    assert HYPERCORE_CHAIN_ID in chains, f"Hypercore chain not in price data, got chains: {chains}"
    assert 42161 in chains, f"Arbitrum chain not in price data, got chains: {chains}"

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
