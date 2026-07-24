"""ApeX shared vault-pipeline export tests."""

# ruff: noqa: DTZ001

import datetime
from pathlib import Path

import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.apex.vault import ApexHistoryPoint, ApexVaultSummary
from eth_defi.apex.vault_data_export import build_raw_prices_dataframe, create_apex_vault_row, merge_into_vault_database
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.utils import is_good_multichain_address
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_TVL = 125.0


def _vault(vault_id: str = "2044287989957394432") -> ApexVaultSummary:
    """Create one deterministic ApeX metadata record for export tests.

    :param vault_id:
        ApeX platform identity.
    :return:
        Populated current vault summary.
    """
    observed_at = datetime.datetime(2026, 7, 23, 12)
    return ApexVaultSummary(
        vault_id=vault_id,
        synthetic_address=f"apex-vault-{vault_id}",
        reported_ethereum_address="0xdb246af9ef918be85ea7cf98925480ff367a7038",
        name="Market maker",
        description="Perpetual futures market-making strategy.",
        status="VAULT_IN_PROCESS",
        vault_type="NOT_COLLECT_VAULT",
        share_price=1.25,
        tvl=EXPECTED_TVL,
        share_count=100.0,
        created_at=observed_at - datetime.timedelta(days=10),
        source_updated_at=observed_at,
        finished_at=None,
        max_amount=1000.0,
        purchase_fee_rate_raw="0",
        share_profit_ratio_raw="20",
    )


def test_apex_synthetic_identity_is_a_shared_vault_spec() -> None:
    """Expose ApeX synthetic identities through the shared metadata model.

    The platform vault ID, rather than the non-unique Ethereum metadata
    address, must remain the shared pipeline identity.
    """
    vault = _vault()
    spec, row = create_apex_vault_row(
        vault_id=vault.vault_id,
        name=vault.name,
        description=vault.description,
        tvl=vault.tvl,
        share_count=vault.share_count,
        created_at=vault.created_at,
        first_seen=datetime.datetime(2026, 7, 23, 12),
        status=vault.status,
    )

    assert is_good_multichain_address(vault.synthetic_address)
    assert spec == VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=vault.synthetic_address)
    assert row["Protocol"] == "ApeX"
    assert row["_fees"].fee_mode is None
    assert row["Perf fee"] is None
    assert get_vault_protocol_name({ERC4626Feature.apex_native}) == "ApeX"


def test_apex_duckdb_exports_metadata_and_exact_timestamp_prices(tmp_path: Path) -> None:
    """Merge one ApeX vault into shared metadata and raw price formats.

    History timestamps remain untouched and unrelated shared metadata survives
    the idempotent ApeX upsert.
    """
    database = ApexMetricsDatabase(tmp_path / "apex-vaults.duckdb")
    observed_at = datetime.datetime(2026, 7, 23, 12)
    history_at = observed_at - datetime.timedelta(hours=3, minutes=17)
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    existing_spec = VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001")
    existing = VaultDatabase()
    existing.rows[existing_spec] = {"Name": "Existing"}
    existing.write(vault_db_path)

    try:
        vault = _vault()
        database.apply_ranking((vault,), observed_at, manage_disappearance=True)
        database.apply_history_success(
            vault.vault_id,
            (ApexHistoryPoint(timestamp=history_at, net_value=1.2, total_value=120.0),),
            observed_at + datetime.timedelta(minutes=1),
        )

        prices = build_raw_prices_dataframe(database)
        merged = merge_into_vault_database(database, vault_db_path)
        merged_again = merge_into_vault_database(database, vault_db_path)
    finally:
        database.close()

    apex_spec = VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=vault.synthetic_address)
    assert set(prices["chain"]) == {APEX_CHAIN_ID}
    assert history_at in set(prices["timestamp"])
    assert prices.loc[prices["timestamp"] == history_at, "share_price"].iloc[0] == pytest.approx(1.2)
    assert prices.loc[prices["timestamp"] == history_at, "total_supply"].iloc[0] == pytest.approx(100.0)
    assert existing_spec in merged.rows
    assert apex_spec in merged.rows
    assert merged.rows[apex_spec]["NAV"] == EXPECTED_TVL
    assert set(merged_again.rows) == {existing_spec, apex_spec}
