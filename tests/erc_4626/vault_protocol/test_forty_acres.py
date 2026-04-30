"""Test 40acres vault metadata.

40acres is a cashflow lending protocol for veNFT collateral
with ERC-4626 USDC supply vaults on Avalanche, Base, and Optimism.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

pytestmark = pytest.mark.skipif(
    JSON_RPC_AVALANCHE is None,
    reason="JSON_RPC_AVALANCHE needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_avalanche_fork(request) -> AnvilLaunch:
    """Fork Avalanche at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_AVALANCHE, fork_block_number=84244698)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_avalanche_fork: AnvilLaunch):
    web3 = create_multi_provider_web3(anvil_avalanche_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_forty_acres_blackhole(
    web3: Web3,
    tmp_path: Path,
):
    """Read 40acres Blackhole USDC vault metadata on Avalanche.

    1. Auto-detect the vault protocol from the hardcoded address
    2. Verify the vault instance type and protocol name
    3. Check fee methods return None (fees are protocol-embedded)
    """

    # 1. Auto-detect the vault protocol
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd",
    )

    # 2. Verify instance type and protocol name
    assert isinstance(vault, FortyAcresVault)
    assert vault.get_protocol_name() == "40acres"
    assert ERC4626Feature.forty_acres_like in vault.features

    # 3. Check fee methods
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
