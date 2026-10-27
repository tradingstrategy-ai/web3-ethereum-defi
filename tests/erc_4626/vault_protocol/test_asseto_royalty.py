"""Read the deployed Soneium IP Vault through a shared, pinned Anvil fork."""

# Fixed-block token decimals are exact characterisation values.
# ruff: noqa: PLR2004

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import SONEIUM_MIDNIGHT_BLOCK
from eth_defi.tokenised_fund.asseto.royalty import AssetoRoyaltyVault
from eth_defi.tokenised_fund.asseto.royalty_constants import ROYALTY_ASSET_ADDRESS, ROYALTY_DEPLOYMENT_BLOCK, ROYALTY_VAULT_ADDRESS
from eth_defi.tokenised_fund.asseto.royalty_migration import migrate_royalty_metadata
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_SONEIUM = os.environ.get("JSON_RPC_SONEIUM")
pytestmark = [
    pytest.mark.skipif(not JSON_RPC_SONEIUM, reason="JSON_RPC_SONEIUM required"),
    pytest.mark.xdist_group("fork:soneium:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse a read-only fork after the IP Vault deployment."""
    return anvil_fork_pool.get_web3(JSON_RPC_SONEIUM, SONEIUM_MIDNIGHT_BLOCK)


def test_asseto_royalty_onchain_reader_and_metadata(web3: Web3) -> None:
    """Verify identity, scales, supported calls and the real migration dry run."""
    vault = create_vault_instance_autodetect(web3, ROYALTY_VAULT_ADDRESS)
    assert isinstance(vault, AssetoRoyaltyVault)
    assert vault.get_protocol_name() == "Asseto"
    assert vault.name == "Staked IPUSD"
    assert vault.symbol == "sIPUSD"
    assert vault.share_token.decimals == 6
    assert vault.denomination_token.address.lower() == ROYALTY_ASSET_ADDRESS
    assert vault.denomination_token.symbol == "IPUSD"
    assert vault.denomination_token.decimals == 6
    assert vault.fetch_total_assets(SONEIUM_MIDNIGHT_BLOCK) == Decimal(0)
    assert vault.fetch_total_supply(SONEIUM_MIDNIGHT_BLOCK) == Decimal(0)
    assert vault.fetch_share_price(SONEIUM_MIDNIGHT_BLOCK) == Decimal(1)
    assert vault.get_deposit_manager_capability() is None
    assert not vault.supports_generic_deposit_manager()

    vault.first_seen_at_block = ROYALTY_DEPLOYMENT_BLOCK
    reader = vault.get_historical_reader(stateful=False)
    assert {name for name, _, _ in reader.get_warmup_calls()} == {"total_assets", "total_supply", "convertToAssets"}
    calls = list(reader.construct_multicalls())
    assert {call.func_name for call in calls} == {"totalAssets", "totalSupply", "convertToAssets", "oraclesFresh"}
    results = [call.call_as_result(web3, SONEIUM_MIDNIGHT_BLOCK) for call in calls]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(SONEIUM_MIDNIGHT_BLOCK)["timestamp"], datetime.UTC).replace(tzinfo=None)
    assert timestamp == datetime.datetime(2026, 10, 26, 23, 59, 59, tzinfo=datetime.UTC).replace(tzinfo=None)
    observation = reader.process_result(SONEIUM_MIDNIGHT_BLOCK, timestamp, results)
    assert observation.share_price == Decimal(1)
    assert observation.total_assets == Decimal(0)
    assert observation.total_supply == Decimal(0)
    assert observation.max_deposit is None

    # A stale oracle must not be reissued as a current valuation.
    freshness = next(result for result in results if result.call.func_name == "oraclesFresh")
    freshness.result = bytes(32)
    stale = reader.process_result(SONEIUM_MIDNIGHT_BLOCK, timestamp, results)
    assert stale.share_price is None
    assert stale.total_assets is None

    database = VaultDatabase()
    row = migrate_royalty_metadata(database, web3, dry_run=True)
    assert not database.rows
    assert row["Protocol"] == "Asseto"
    assert row["Symbol"] == "sIPUSD"
    assert row["Denomination"] == "IPUSD"
    assert VaultFlag.review_needed in row["_flags"]
    assert row["_deposit_manager"] is None
    assert row["_deposit_permission"] == "unknown"
    assert "under verification" in row["_notes"]
