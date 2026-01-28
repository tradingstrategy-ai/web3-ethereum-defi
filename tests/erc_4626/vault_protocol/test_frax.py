"""Test Frax vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.frax.vault import FraxVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_331_904)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_frax(
    web3: Web3,
    tmp_path: Path,
):
    """Read Frax Fraxlend vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xeE847a804b67f4887c9e8fe559a2da4278defb52",
    )

    assert isinstance(vault, FraxVault)
    assert vault.get_protocol_name() == "Frax"
    assert vault.features == {ERC4626Feature.frax_like}
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.10
    assert vault.has_custom_fees() is False
    assert vault.get_risk() == VaultTechnicalRisk.low
