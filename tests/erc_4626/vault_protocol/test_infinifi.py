"""Test infiniFi vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.infinifi.vault import InfiniFiVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_263_313)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_infinifi(
    web3: Web3,
    tmp_path: Path,
):
    """Read infiniFi siUSD vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xdbdc1ef57537e34680b898e1febd3d68c7389bcb",
    )

    assert isinstance(vault, InfiniFiVault)
    assert vault.get_protocol_name() == "infiniFi"
    assert vault.features == {ERC4626Feature.infinifi_like}

    # Fee assertions
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None  # Not publicly documented
    assert vault.has_custom_fees() is False

    # Check the link
    assert vault.get_link() == "https://app.infinifi.xyz/deposit"
