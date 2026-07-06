"""Test T3tris vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

FORK_BLOCK = 480_900_000

#: Gami USDC vault on Arbitrum, listed in the T3tris app.
GAMI_USDC_VAULT = "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f"

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork() -> AnvilLaunch:
    """Fork Arbitrum at a specific block for reproducibility.

    Gami USDC is a live T3tris vault at the pinned block. The fixed block pins
    the classification probe response and fee configuration values.
    """

    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork) -> Web3:
    """Create Web3 connection to the Arbitrum fork."""

    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url, retries=2)


@flaky.flaky
def test_t3tris_gami_usdc(web3: Web3) -> None:
    """Read T3tris vault metadata on a fixed Arbitrum fork."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=GAMI_USDC_VAULT,
    )

    assert isinstance(vault, T3trisVault)
    assert vault.get_protocol_name() == "T3tris"
    assert vault.features == {ERC4626Feature.t3tris_like}
    assert vault.address == GAMI_USDC_VAULT
    assert vault.vault_address == GAMI_USDC_VAULT

    assert vault.name == "Gami USDC"
    assert vault.symbol == "gamiusdc"
    assert vault.fetch_denomination_token_address() == "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert vault.fetch_total_assets(FORK_BLOCK) > 0
    assert vault.fetch_total_supply(FORK_BLOCK) > 0

    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting
    assert vault.get_management_fee(FORK_BLOCK) == 0.0
    assert vault.get_performance_fee(FORK_BLOCK) == pytest.approx(0.2)
    assert vault.get_deposit_fee(FORK_BLOCK) == 0.0
    assert vault.get_withdraw_fee(FORK_BLOCK) == 0.0

    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode == VaultFeeMode.internalised_minting
    assert fee_data.management == 0.0
    assert fee_data.performance == pytest.approx(0.2)
    assert fee_data.deposit == 0.0
    assert fee_data.withdraw == 0.0

    gross_tvl, gross_managed_assets, gross_pending_deposits, gross_claimable_redeems = vault.vault_contract.functions.getGrossTVL().call(block_identifier=FORK_BLOCK)
    assert gross_tvl > 0
    assert gross_managed_assets > 0
    assert gross_pending_deposits >= 0
    assert gross_claimable_redeems >= 0

    assert vault.get_link() == f"https://app.t3tris.finance/vaults?chainId=42161&address={Web3.to_checksum_address(GAMI_USDC_VAULT)}"
