"""Test Nest vault classification and first-party metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import fetch_nest_vaults
from eth_defi.erc_4626.vault_protocol.nest.vault import NestVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import AVALANCHE_MIDNIGHT_BLOCK

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

# Nest BlackOpal LiquidStone II Vault nOPAL USDC route on Avalanche.
NEST_N_OPAL_AVALANCHE_VAULT = "0xd258029cf5a177e3306e09fbea63424543a505c0"
NEST_N_OPAL_SLUG = "nest-opal-vault"
NEST_N_OPAL_START_BLOCK = 90_379_027
NEST_N_OPAL_REDEMPTION_TIME_DAYS = 4


pytestmark = [
    pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run these tests"),
    # Shared with the other Avalanche midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:avalanche:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create a read-only shared Avalanche fork at the fixed midnight block.

    :param anvil_fork_pool:
        Session-scoped shared Anvil fork pool.

    :return:
        Web3 client connected to the shared Avalanche fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_AVALANCHE, AVALANCHE_MIDNIGHT_BLOCK)


@flaky.flaky
def test_nest_nopal_vault(web3: Web3) -> None:
    """Identify the nOPAL NestVault using its unique one-call probe.

    ``totalPendingShares()`` is the Nest-specific, no-argument probe.  The
    generic classifier independently identifies the ERC-7540 and ERC-7575
    interfaces exposed by the same contract.

    :param web3:
        Shared fixed-block Avalanche fork client.
    """
    vault = create_vault_instance_autodetect(web3, vault_address=NEST_N_OPAL_AVALANCHE_VAULT)

    assert isinstance(vault, NestVault)
    assert vault.get_protocol_name() == "Nest"
    assert ERC4626Feature.nest_like in vault.features
    assert ERC4626Feature.erc_7540_like in vault.features
    assert ERC4626Feature.erc_7575_like in vault.features
    assert vault.get_deposit_manager_capability() is None

    assert vault.nest_metadata is not None
    assert vault.nest_metadata["symbol"] == "nOPAL"
    assert vault.nest_metadata["slug"] == NEST_N_OPAL_SLUG
    assert vault.description is not None
    assert vault.get_estimated_lock_up().days == NEST_N_OPAL_REDEMPTION_TIME_DAYS
    assert vault.fetch_total_pending_shares() >= 0
    assert vault.get_link() == "https://www.nest.credit/vaults#vaults-explore"


def test_fetch_nest_vaults(tmp_path: Path) -> None:
    """Fetch Nest's first-party API and CMS metadata into a local cache.

    :param tmp_path:
        Isolated filesystem location supplied by pytest.
    """
    vaults = fetch_nest_vaults(cache_path=tmp_path)

    nopal = vaults.get(f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}")
    assert nopal is not None
    assert nopal["name"] == "Nest BlackOpal LiquidStone II Vault"
    assert nopal["asset_symbol"] == "USDC"
    assert nopal["share_token_address"] == "0x119Dd7dAFf816f29D7eE47596ae5E4bdC4299165"
    assert nopal["start_block"] == NEST_N_OPAL_START_BLOCK
    assert nopal["description"] is not None
    assert nopal["redemption_time_days"] == NEST_N_OPAL_REDEMPTION_TIME_DAYS
    assert (tmp_path / "nest_vaults.json").exists()
