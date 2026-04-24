"""Test Gauntlet vault metadata.

Tests Gauntlet vault detection for both VaultV2 (Aera V2, detected via adapterRegistry)
and MultiDepositorVault (Aera V3, hardcoded addresses).
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gauntlet.vault import GauntletVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24949000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 instance connected to forked Ethereum."""
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_gauntlet_vault_v2(
    web3: Web3,
    tmp_path: Path,
):
    """Detect Gauntlet vault using VaultV2 (Aera V2) contract.

    1. Autodetect vault via adapterRegistry() probe
    2. Verify protocol name is Gauntlet
    3. Read management and performance fees
    4. Verify link generation
    """

    # 1. Autodetect Gauntlet USDC Prime v2 on Ethereum
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x8c106eedad96553e64287a5a6839c3cc78afa3d0",
    )

    # 2. Check type and protocol
    assert isinstance(vault, GauntletVault)
    assert vault.features == {ERC4626Feature.gauntlet_like}
    assert vault.get_protocol_name() == "Gauntlet"

    # 3. Read fees from the contract
    mgmt_fee = vault.get_management_fee("latest")
    perf_fee = vault.get_performance_fee("latest")
    assert mgmt_fee is not None
    assert perf_fee is not None
    assert mgmt_fee >= 0
    assert perf_fee >= 0

    # 4. Check link
    link = vault.get_link()
    assert "app.gauntlet.xyz" in link

    # 5. Check lock-up
    assert vault.get_estimated_lock_up().days == 0
