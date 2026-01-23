"""Test aarnâ vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.aarna.vault import AarnaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24296796)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_aarna(
    web3: Web3,
    tmp_path: Path,
):
    """Read aarnâ vault metadata.

    https://etherscan.io/address/0xb9c1344105faa4681bc7ffd68c5c526da61f2ae8
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xb9c1344105faa4681bc7ffd68c5c526da61f2ae8",
    )

    assert isinstance(vault, AarnaVault)
    assert vault.get_protocol_name() == "aarnâ"
    assert vault.features == {ERC4626Feature.aarna_like}

    # Check vault name
    assert "aarnâ" in vault.name or "atv" in vault.name

    # Fee information not publicly documented
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Risk not yet assessed
    assert vault.get_risk() is None

    # Link should point to the app
    assert vault.get_link() == "https://engine.aarna.ai/"
