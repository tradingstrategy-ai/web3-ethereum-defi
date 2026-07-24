"""Test Superform vault blacklist detection on Ethereum mainnet.

Tests that vaults flagged as blacklisted due to lack of transparency are correctly detected.
"""

import os

import pytest
from web3 import Web3

from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.risk import get_vault_risk, VAULT_SPECIFIC_RISK

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


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
