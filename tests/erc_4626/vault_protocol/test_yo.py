"""Test Yo vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yo.vault import YoVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24303785)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_yo_vault(web3: Web3):
    """Read Yo vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0000000f2eb9f69274678c76222b35eec7588a65",
    )

    assert isinstance(vault, YoVault)
    assert vault.get_protocol_name() == "Yo"
    assert vault.features == {ERC4626Feature.yo_like}

    # Yo vault has custom deposit/withdrawal fees
    assert vault.has_custom_fees() is True

    # Management and performance fees are not applicable for Yo
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # Check the vault link
    assert vault.get_link() == "https://www.yo.xyz/"
