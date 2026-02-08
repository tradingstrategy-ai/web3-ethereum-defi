"""Test sBOLD vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.sbold.vault import SBOLDVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_411_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_sbold(
    web3: Web3,
    tmp_path: Path,
):
    """Read sBOLD vault metadata.

    https://etherscan.io/address/0x50bd66d59911f5e086ec87ae43c811e0d059dd11
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x50bd66d59911f5e086ec87ae43c811e0d059dd11",
    )

    assert isinstance(vault, SBOLDVault)
    assert vault.get_protocol_name() == "sBOLD"
    assert vault.features == {ERC4626Feature.sbold_like}

    # Verify vault name and symbol
    assert vault.name == "sBold"
    assert vault.symbol == "sBOLD"

    # Verify the underlying asset is BOLD
    assert vault.denomination_token.symbol == "BOLD"

    # Check fee information
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check no lock-up
    assert vault.get_estimated_lock_up().days == 0

    # Check link
    assert vault.get_link() == "https://www.k3.capital/"
