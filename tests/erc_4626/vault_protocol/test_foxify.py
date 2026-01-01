"""Test Foxify vault metadata."""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.foxify.vault import FoxifyVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_SONIC = os.environ.get("JSON_RPC_SONIC")

pytestmark = pytest.mark.skipif(JSON_RPC_SONIC is None, reason="JSON_RPC_SONIC needed to run these tests")


@pytest.fixture(scope="module")
def anvil_sonic_fork(request) -> AnvilLaunch:
    """Fork Sonic at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_SONIC, fork_block_number=58_072_500)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_sonic_fork):
    web3 = create_multi_provider_web3(anvil_sonic_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_foxify(
    web3: Web3,
    tmp_path: Path,
):
    """Read Foxify vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3ccff8c929b497c1ff96592b8ff592b45963e732",
    )

    assert isinstance(vault, FoxifyVault)
    assert vault.get_protocol_name() == "Foxify"
    assert vault.features == {ERC4626Feature.foxify_like}
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False
