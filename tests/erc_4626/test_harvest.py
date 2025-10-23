"""Harvest vault tests"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.harvest.vault import HarvestVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultRisk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Read gmUSDC vault at a specific block"""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=392_313_989)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_harvest(
    web3: Web3,
    tmp_path: Path,
):
    """Read Harvest vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x5e777587d6f9261a85d7f062790d4cee71081ba1",
    )

    assert isinstance(vault, HarvestVault)

    assert vault.get_risk() == VaultRisk.lowish
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.12
    assert vault.has_custom_fees() is False
    assert vault.get_protocol_name() == "Plutus"
