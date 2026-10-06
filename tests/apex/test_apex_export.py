"""ApeX shared vault-pipeline export tests."""

# ruff: noqa: DTZ001

import datetime
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.apex.session import create_apex_session_pool
from eth_defi.apex.vault import ApexHistoryPoint, ApexVaultSummary, fetch_vault_fees
from eth_defi.apex.vault_data_export import build_raw_prices_dataframe, create_apex_fee_data, create_apex_vault_row, get_apex_vault_link, merge_into_vault_database
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.research.vault_metrics import calculate_net_profit
from eth_defi.utils import is_good_multichain_address
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_TVL = 125.0
MAX_CREATOR_PROFIT_SHARE = 0.1


@pytest.mark.parametrize(
    ("vault_id", "expected_url"),
    [
        ("2099816991878676480", "https://omni.apex.exchange/vaultInfo/2099816991878676480"),
        (" 10000 ", "https://omni.apex.exchange/vaultInfo/10000/1"),
        ("10001", "https://omni.apex.exchange/vaultInfo/10001/1"),
    ],
)
def test_apex_vault_link_selects_the_correct_view(vault_id: str, expected_url: str) -> None:
    """User vaults use their own view; both official vaults retain ``/1``.

    The user-vault identity is the live AI multistrategy regression case.

    :param vault_id:
        User-created or official ApeX identity.
    :param expected_url:
        Direct page URL used by the native application.
    """
    assert get_apex_vault_link(vault_id) == expected_url


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
        redemption_delay=datetime.timedelta(days=1),
    )

    assert is_good_multichain_address(vault.synthetic_address)
    assert spec == VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=vault.synthetic_address)
    assert row["Protocol"] == "ApeX"
    assert row["Link"] == "https://omni.apex.exchange/vaultInfo/2044287989957394432"
    assert row["_fees"].fee_mode == VaultFeeMode.externalised
    assert row["Perf fee"] is None
    assert not row["_fees"].can_calculate_investor_net_performance()
    assert row["_lockup"] == datetime.timedelta(days=1)
    assert row["_strategy_tags"] == {
        StrategyTag.discretionary_trading,
        StrategyTag.perpetual_futures,
    }
    assert get_vault_protocol_name({ERC4626Feature.apex_native}) == "ApeX"


@pytest.mark.parametrize(("end_price", "expected_net_return"), ((1.2, 0.18), (0.8, -0.2)))
def test_apex_profit_share_is_charged_once_on_positive_returns(end_price: float, expected_net_return: float) -> None:
    """Apply the creator's 10% only to investor profits at redemption.

    A 20% NAV gain leaves 18% for the depositor, while losses remain unchanged.
    This regression uses the shared calculation also used for Hyperliquid.

    :param end_price:
        NAV at the end of a subscription beginning at NAV 1.
    :param expected_net_return:
        Investor return after the creator's profit share.
    :return:
        None.
    """
    fees = create_apex_fee_data("2099816991878676480", 0.1, 0.0)
    net_fees = fees.get_net_fees()
    result = calculate_net_profit(
        start=datetime.datetime(2026, 9, 15),
        end=datetime.datetime(2026, 10, 15),
        share_price_start=1.0,
        share_price_end=end_price,
        management_fee_annual=net_fees.management,
        performance_fee=net_fees.performance,
        deposit_fee=net_fees.deposit,
        withdrawal_fee=net_fees.withdraw,
    )
    assert result == pytest.approx(expected_net_return)


@pytest.mark.parametrize("vault_id", ("10000", "10001"))
def test_apex_official_vaults_export_a_complete_zero_fee_schedule(vault_id: str) -> None:
    """Keep protocol fee-revenue vaults separate from creator profit sharing.

    Both curated protocol vaults return their accrued yield without a
    user-vault creator deduction.

    :param vault_id:
        Curated official protocol or new-user vault identity.
    :return:
        None.
    """
    fees = create_apex_fee_data(vault_id, None, None)
    assert fees.fee_mode == VaultFeeMode.feeless
    assert fees.performance == fees.management == fees.deposit == fees.withdraw == 0.0
    assert fees.can_calculate_investor_net_performance()


@pytest.mark.live
@pytest.mark.timeout(60)
def test_live_apex_profile_fees_reach_shared_exports(tmp_path: Path) -> None:
    """Fetch real AI multistrategy fees and carry them through shared exports.

    This public, unauthenticated integration check reads one profile and
    writes only temporary test state. It exercises the actual provider,
    DuckDB persistence, metadata and raw-price exports, and the investor fee
    calculation without submitting a deposit or redemption.

    :param tmp_path:
        Temporary directory isolating all database and metadata writes.
    :return:
        None.
    """
    vault_id = "2099816991878676480"
    with create_apex_session_pool(pool_maxsize=1, retries=0) as session_pool:
        fees = fetch_vault_fees(session_pool, vault_id, operation_timeout=30)
    assert fees.fee_mode == VaultFeeMode.externalised
    assert 0 <= fees.performance <= MAX_CREATOR_PROFIT_SHARE
    assert fees.can_calculate_investor_net_performance()
    database = ApexMetricsDatabase(tmp_path / "apex.duckdb")
    try:
        vault = _vault(vault_id)
        database.apply_ranking((vault,), datetime.datetime(2026, 10, 6), manage_disappearance=True, fee_data={vault_id: fees})
        metadata = merge_into_vault_database(database, tmp_path / "vault-metadata-db.pickle")
        prices = build_raw_prices_dataframe(database)
    finally:
        database.close()
    exported = metadata.rows[VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=f"apex-vault-{vault_id}")]["_fees"]
    assert exported.performance == pytest.approx(fees.performance)
    assert prices["performance_fee"].iloc[0] == pytest.approx(fees.performance)
    assert prices["management_fee"].iloc[0] == 0.0
    investor_fees = exported.get_net_fees()
    result = calculate_net_profit(
        start=datetime.datetime(2026, 9, 15),
        end=datetime.datetime(2026, 10, 15),
        share_price_start=1.0,
        share_price_end=1.2,
        management_fee_annual=investor_fees.management,
        performance_fee=investor_fees.performance,
        deposit_fee=investor_fees.deposit,
        withdrawal_fee=investor_fees.withdraw,
    )
    assert result == pytest.approx(0.2 * (1 - fees.performance))


def test_apex_export_normalises_and_validates_vault_id() -> None:
    """Use a canonical non-empty platform identity for the direct link."""
    _, row = create_apex_vault_row(
        vault_id=" 10001 ",
        name="ApeX vault",
        description=None,
        tvl=None,
        share_count=None,
        created_at=None,
        first_seen=datetime.datetime(2026, 7, 23, 12),
        status="VAULT_IN_PROCESS",
    )

    assert row["Link"] == "https://omni.apex.exchange/vaultInfo/10001/1"
    with pytest.raises(ValueError, match="vault ID is required"):
        create_apex_vault_row(
            vault_id=" ",
            name="ApeX vault",
            description=None,
            tvl=None,
            share_count=None,
            created_at=None,
            first_seen=datetime.datetime(2026, 7, 23, 12),
            status="VAULT_IN_PROCESS",
        )


def test_apex_duckdb_exports_metadata_and_exact_timestamp_prices(tmp_path: Path) -> None:
    """Merge known and unknown ApeX fees into shared metadata and price rows.

    History timestamps remain untouched and unrelated shared metadata survives
    the idempotent ApeX upsert. Unknown fees must remain null in both exports
    and suppress investor net performance.

    :param tmp_path:
        Temporary directory isolating all database and metadata writes.
    :return:
        None.
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
        unknown_fee_vault = _vault("unknown-fees")
        database.apply_ranking(
            (vault, unknown_fee_vault),
            observed_at,
            manage_disappearance=True,
            redemption_delays={vault.vault_id: datetime.timedelta(days=1)},
            fee_data={vault.vault_id: create_apex_fee_data(vault.vault_id, 0.05, 0.0)},
        )
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
    unknown_spec = VaultSpec(chain_id=APEX_CHAIN_ID, vault_address=unknown_fee_vault.synthetic_address)
    known_prices = prices.loc[prices["address"] == vault.synthetic_address]
    unknown_prices = prices.loc[prices["address"] == unknown_fee_vault.synthetic_address]
    assert set(prices["chain"]) == {APEX_CHAIN_ID}
    assert history_at in set(prices["timestamp"])
    assert prices.loc[prices["timestamp"] == history_at, "share_price"].iloc[0] == pytest.approx(1.2)
    assert prices.loc[prices["timestamp"] == history_at, "total_supply"].iloc[0] == pytest.approx(100.0)
    assert known_prices["performance_fee"].tolist() == pytest.approx([0.05, 0.05])
    assert known_prices["management_fee"].tolist() == [0.0, 0.0]
    assert pd.isna(unknown_prices["performance_fee"].iloc[0])
    assert merged.rows[unknown_spec]["Perf fee"] is None
    assert not merged.rows[unknown_spec]["_fees"].can_calculate_investor_net_performance()
    assert existing_spec in merged.rows
    assert apex_spec in merged.rows
    assert merged.rows[apex_spec]["NAV"] == EXPECTED_TVL
    assert merged.rows[apex_spec]["_lockup"] == datetime.timedelta(days=1)
    assert merged.rows[apex_spec]["Perf fee"] == pytest.approx(0.05)
    assert merged.rows[apex_spec]["_fees"].can_calculate_investor_net_performance()
    assert set(merged_again.rows) == {existing_spec, apex_spec, unknown_spec}


def test_apex_official_vault_export_uses_curated_descriptions(tmp_path: Path) -> None:
    """Replace the official API placeholder text with reviewed protocol copy."""
    database = ApexMetricsDatabase(tmp_path / "apex-vaults.duckdb")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    observed_at = datetime.datetime(2026, 7, 23, 12)
    official = _vault("10000")
    official = replace(official, name="Source API name", description="Source API placeholder")
    try:
        database.apply_ranking((official,), observed_at, manage_disappearance=True)
        merged = merge_into_vault_database(database, vault_db_path)
        prices = build_raw_prices_dataframe(database)
    finally:
        database.close()

    row = merged.rows[VaultSpec(chain_id=APEX_CHAIN_ID, vault_address="apex-vault-10000")]
    assert row["Name"] == "Protocol Vault"
    assert "flagship official vault" in row["_description"]
    assert row["_short_description"].startswith("ApeX Omni's flagship")
    assert row["_lockup"] is None
    assert row["Perf fee"] == row["Mgmt fee"] == row["Deposit fee"] == row["Withdraw fee"] == 0.0
    assert row["_fees"].fee_mode == VaultFeeMode.feeless
    assert row["_fees"].can_calculate_investor_net_performance()
    assert prices["performance_fee"].iloc[0] == prices["management_fee"].iloc[0] == 0.0
    assert row["_strategy_tags"] == {
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.perpetual_futures,
    }
