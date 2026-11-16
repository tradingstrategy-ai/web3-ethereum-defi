"""Characterise both Birch Hill vaults against their real Base contracts."""

import os

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.erc_4626.vault_protocol.yearn.vault import BIRCH_HILL_DEPOSIT_HOOK, BIRCH_HILL_RWA_USDC, YearnV3Vault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: Flagship has no contract at July's BASE_MIDNIGHT_BLOCK; pin its reviewed November state.
BIRCH_HILL_FORK_BLOCK = 54_012_303

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE required"),
    pytest.mark.xdist_group("fork:base:birch-hill"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse a fixed November fork because the Flagship postdates July.

    These tests only read state, so per-test snapshot isolation is unnecessary.

    :param anvil_fork_pool: Shared session fork pool with persistent RPC cache.
    :return: Base Web3 at the reviewed block.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BIRCH_HILL_FORK_BLOCK)


def test_birch_hill_real_deposit_policies(web3: Web3) -> None:
    """Distinguish the real whitelist hook from the ungated Ecosystem vault.

    Exercise the production scanner resolver with the verified deployment ABI
    and confirm both Morpho deposit gate slots are disabled.

    :param web3: Shared read-only Base fork.
    """
    flagship = create_vault_instance_autodetect(web3, BIRCH_HILL_RWA_USDC.vault_address)
    assert type(flagship) is YearnV3Vault
    assert flagship.vault_contract.functions.deposit_hook().call().lower() == BIRCH_HILL_DEPOSIT_HOOK
    assert fetch_deposit_permission(flagship) is VaultDepositPermission.whitelisted
    assert flagship.get_whitelist_notes() is None
    ecosystem = MorphoV2Vault(web3, VaultSpec(8453, "0xb038cf3e233b78225f6d350ac8319bec8457db4a"))
    assert ecosystem.fetch_deposit_gates() == (ZERO_ADDRESS_STR, ZERO_ADDRESS_STR)
    assert fetch_deposit_permission(ecosystem) is VaultDepositPermission.permissionless
