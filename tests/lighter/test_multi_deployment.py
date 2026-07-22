"""Test deployment-aware Lighter metrics storage and export."""

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pandas as pd

from eth_defi.lighter.constants import (
    LIGHTER_CHAIN_ID,
    LIGHTER_ETHEREUM,
    LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID,
    LIGHTER_ROBINHOOD,
    LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX,
    identify_lighter_pool_deployment,
)
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.vault import fetch_all_pools
from eth_defi.lighter.vault_data_export import build_raw_prices_dataframe, create_lighter_pool_row, merge_into_uncleaned_parquet, merge_into_vault_database
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

ACCOUNT_INDEX = 281474976710654


def test_synthetic_pool_addresses_resolve_to_exact_deployment() -> None:
    """Do not let the shorter Ethereum prefix capture Robinhood addresses."""
    ethereum_address = LIGHTER_ETHEREUM.format_pool_address(ACCOUNT_INDEX)
    robinhood_address = LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX)

    assert identify_lighter_pool_deployment(ethereum_address) == LIGHTER_ETHEREUM
    assert identify_lighter_pool_deployment(robinhood_address) == LIGHTER_ROBINHOOD
    assert identify_lighter_pool_deployment("lighter-pool-robinhood-not-an-index") is None
    assert identify_lighter_pool_deployment("lighter-pool-robinhood-123-extra") is None


def test_robinhood_llp_override_does_not_misclassify_other_type_three_pools() -> None:
    """Use the Robinhood LLP override without treating every type-three pool as LLP."""
    system_config_response = MagicMock()
    system_config_response.json.return_value = {"liquidity_pool_index": 281474976710655}

    pool_listing_response = MagicMock()
    pool_listing_response.json.return_value = {
        "public_pools": [
            {
                "account_index": LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX,
                "account_type": 3,
                "name": "",
                "total_asset_value": "1000",
            },
            {
                "account_index": LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX - 1,
                "account_type": 3,
                "name": "Another protocol liquidity pool",
                "total_asset_value": "500",
            },
        ]
    }

    session = LighterSession(deployment=LIGHTER_ROBINHOOD)
    session.get = MagicMock(side_effect=[system_config_response, pool_listing_response])
    try:
        pools = fetch_all_pools(session)
    finally:
        session.close()

    llp_pools = [pool for pool in pools if pool.is_llp]
    assert len(llp_pools) == 1
    assert llp_pools[0].account_index == LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX


def _create_legacy_database(path: Path) -> None:
    """Create the pre-multi-deployment Lighter DuckDB schema.

    :param path:
        Database path to create.
    """
    con = duckdb.connect(str(path))
    try:
        con.execute("""
            CREATE TABLE pool_metadata (
                account_index BIGINT PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                l1_address VARCHAR,
                is_llp BOOLEAN DEFAULT FALSE,
                status INTEGER DEFAULT 0,
                operator_fee DOUBLE,
                total_asset_value DOUBLE,
                annual_percentage_yield DOUBLE,
                sharpe_ratio DOUBLE,
                created_at TIMESTAMP,
                last_updated TIMESTAMP NOT NULL
            )
        """)
        con.execute(
            "INSERT INTO pool_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ACCOUNT_INDEX,
                "Ethereum LLP",
                "Legacy Ethereum row",
                "0x0000000000000000000000000000000000000000",
                True,
                0,
                0.0,
                1_000.0,
                5.0,
                1.0,
                datetime.datetime(2025, 1, 1),
                datetime.datetime(2026, 7, 1),
            ],
        )
        con.execute("""
            CREATE TABLE pool_daily_prices (
                account_index BIGINT NOT NULL,
                date DATE NOT NULL,
                share_price DOUBLE NOT NULL,
                tvl DOUBLE,
                daily_return DOUBLE,
                annual_percentage_yield DOUBLE,
                written_at TIMESTAMP,
                PRIMARY KEY (account_index, date)
            )
        """)
        con.execute(
            "INSERT INTO pool_daily_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ACCOUNT_INDEX,
                datetime.date(2026, 7, 1),
                1.0,
                1_000.0,
                0.0,
                5.0,
                datetime.datetime(2026, 7, 1),
            ],
        )
    finally:
        con.close()


def test_legacy_database_migrates_to_composite_deployment_keys(tmp_path: Path) -> None:
    """Preserve Ethereum rows while allowing the same Robinhood account index."""
    path = tmp_path / "lighter-pools.duckdb"
    _create_legacy_database(path)

    db = LighterDailyMetricsDatabase(path)
    try:
        migrated_metadata = db.get_all_pool_metadata()
        migrated_prices = db.get_all_daily_prices()
        assert migrated_metadata["deployment"].tolist() == [LIGHTER_ETHEREUM.slug]
        assert migrated_prices["deployment"].tolist() == [LIGHTER_ETHEREUM.slug]

        db.upsert_pool_metadata(
            deployment=LIGHTER_ROBINHOOD.slug,
            account_index=ACCOUNT_INDEX,
            name="Robinhood LLP",
            description="Robinhood deployment row",
            is_llp=True,
            total_asset_value=2_000.0,
            created_at=datetime.datetime(2026, 7, 1),
        )
        db.upsert_daily_prices(
            deployment=LIGHTER_ROBINHOOD.slug,
            rows=[
                (
                    ACCOUNT_INDEX,
                    datetime.date(2026, 7, 1),
                    0.001,
                    2_000.0,
                    0.0,
                    10.0,
                    datetime.datetime(2026, 7, 1),
                )
            ],
        )

        assert db.get_pool_count() == 2
        assert db.get_pool_count(LIGHTER_ETHEREUM.slug) == 1
        assert db.get_pool_count(LIGHTER_ROBINHOOD.slug) == 1

        prices = build_raw_prices_dataframe(db)
        assert LIGHTER_ETHEREUM.chain_id == LIGHTER_ROBINHOOD.chain_id == LIGHTER_CHAIN_ID
        assert set(prices["chain"]) == {LIGHTER_CHAIN_ID}
        assert set(prices["address"]) == {
            LIGHTER_ETHEREUM.format_pool_address(ACCOUNT_INDEX),
            LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX),
        }
    finally:
        db.close()


def test_robinhood_pool_export_uses_deployment_metadata() -> None:
    """Export Robinhood pools with USDG, a distinct address, and Robinhood app link."""
    spec, row = create_lighter_pool_row(
        account_index=ACCOUNT_INDEX,
        name="Lighter Liquidity Provider (LLP)",
        description="Robinhood LLP",
        tvl=123_000.0,
        created_at=datetime.datetime(2026, 7, 1),
        is_llp=True,
        deployment=LIGHTER_ROBINHOOD,
    )

    assert spec.chain_id == LIGHTER_ROBINHOOD.chain_id
    assert spec.vault_address == LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX)
    assert row["Denomination"] == "USDG"
    assert row["_denomination_token"]["symbol"] == "USDG"
    assert row["Link"] == LIGHTER_ROBINHOOD.format_pool_link(ACCOUNT_INDEX)
    assert row["_lockup"] == datetime.timedelta(0)
    assert "USDG-denominated protocol insurance fund" in row["_notes"]
    assert "USDC deposited" not in row["_notes"]
    assert row["_deployment"] == "robinhood"
    assert row["_deployment_chain_id"] == 4663


def test_vault_database_merge_removes_legacy_robinhood_chain(tmp_path: Path) -> None:
    """Move Robinhood metadata from legacy chain 9996 into shared Lighter chain 9998."""
    metrics_path = tmp_path / "lighter-pools.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    metrics_db = LighterDailyMetricsDatabase(metrics_path)
    try:
        metrics_db.upsert_pool_metadata(
            deployment=LIGHTER_ROBINHOOD.slug,
            account_index=ACCOUNT_INDEX,
            name="Robinhood LLP",
            description="Robinhood deployment row",
            is_llp=True,
            total_asset_value=2_000.0,
            created_at=datetime.datetime(2026, 7, 1),
        )
        new_spec, robinhood_row = create_lighter_pool_row(
            account_index=ACCOUNT_INDEX,
            name="Robinhood LLP",
            description="Legacy Robinhood deployment row",
            tvl=2_000.0,
            created_at=datetime.datetime(2026, 7, 1),
            is_llp=True,
            deployment=LIGHTER_ROBINHOOD,
        )
        legacy_spec = VaultSpec(
            chain_id=LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID,
            vault_address=new_spec.vault_address,
        )
        VaultDatabase(rows={legacy_spec: robinhood_row}).write(vault_db_path)

        merged = merge_into_vault_database(metrics_db, vault_db_path)

        assert legacy_spec not in merged.rows
        assert new_spec in merged.rows
        assert len(merged.rows) == 1
        assert merged.rows[new_spec]["_deployment"] == "robinhood"
        assert merged.rows[new_spec]["_deployment_chain_id"] == 4663
    finally:
        metrics_db.close()


def test_standalone_ethereum_parquet_merge_preserves_robinhood_history(tmp_path: Path) -> None:
    """Retain current and legacy Robinhood rows during an Ethereum-only export."""
    metrics_path = tmp_path / "lighter-pools.duckdb"
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    metrics_db = LighterDailyMetricsDatabase(metrics_path)
    try:
        metrics_db.upsert_daily_prices(
            deployment=LIGHTER_ETHEREUM.slug,
            rows=[
                (
                    ACCOUNT_INDEX,
                    datetime.date(2026, 7, 1),
                    1.0,
                    1_000.0,
                    0.0,
                    5.0,
                    datetime.datetime(2026, 7, 1),
                )
            ],
        )
        existing_df = pd.DataFrame(
            {
                "chain": pd.array(
                    [LIGHTER_CHAIN_ID, LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID],
                    dtype="uint32",
                ),
                "address": [
                    LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX),
                    LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX - 1),
                ],
                "timestamp": pd.to_datetime(["2026-06-30", "2026-06-30"]),
                "share_price": [0.001, 0.001],
            }
        )
        VaultHistoricalRead.write_uncleaned_parquet(existing_df, parquet_path)

        merged_df = merge_into_uncleaned_parquet(metrics_db, parquet_path)

        assert set(merged_df["address"]) == {
            LIGHTER_ETHEREUM.format_pool_address(ACCOUNT_INDEX),
            LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX),
            LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX - 1),
        }
        legacy_address = LIGHTER_ROBINHOOD.format_pool_address(ACCOUNT_INDEX - 1)
        assert set(merged_df.loc[merged_df["address"] == legacy_address, "chain"]) == {LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID}
    finally:
        metrics_db.close()
