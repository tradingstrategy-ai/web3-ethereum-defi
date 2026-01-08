"""Test Superform vault blacklist detection on Ethereum mainnet.

Tests that vaults flagged as blacklisted due to lack of transparency are correctly detected.
"""

import os

import pytest
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.risk import get_vault_risk, VAULT_SPECIFIC_RISK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=21_770_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


def test_superform_vault_in_specific_risk():
    """Test that Superform vault is in the VAULT_SPECIFIC_RISK mapping."""
    vault_address = "0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6"
    assert vault_address in VAULT_SPECIFIC_RISK
    assert VAULT_SPECIFIC_RISK[vault_address] == VaultTechnicalRisk.blacklisted


def test_superform_vault_blacklisted(web3: Web3):
    """Test that Superform vault on Ethereum mainnet is detected as blacklisted.

    This vault does not provide adequate transparency about underlying activity
    and positions, making it impossible for users to assess their investment risk.

    See:
    - https://app.superform.xyz/vault/1_0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6
    """
    vault_address = "0x942bed98560e9b2aa0d4ec76bbda7a7e55f6b2d6"

    # Verify vault is blacklisted via get_vault_risk function
    risk = get_vault_risk("Superform", vault_address)
    assert risk == VaultTechnicalRisk.blacklisted
