"""Test Mainstreet Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.mainstreet.vault import MainstreetVault

JSON_RPC_SONIC = os.environ.get("JSON_RPC_SONIC")

pytestmark = pytest.mark.skipif(JSON_RPC_SONIC is None, reason="JSON_RPC_SONIC needed to run these tests")


@pytest.fixture(scope="module")
def anvil_sonic_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_SONIC, fork_block_number=59_684_622)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_sonic_fork):
    web3 = create_multi_provider_web3(anvil_sonic_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_mainstreet_legacy_smsUSD(
    web3: Web3,
    tmp_path: Path,
):
    """Read Mainstreet Finance legacy smsUSD vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc7990369DA608C2F4903715E3bD22f2970536C29",
    )

    assert isinstance(vault, MainstreetVault)
    assert vault.get_protocol_name() == "Mainstreet Finance"
    assert vault.features == {ERC4626Feature.mainstreet_like}

    # Mainstreet has 20% performance fee (10% insurance + 10% treasury)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://mainstreet.finance/"
