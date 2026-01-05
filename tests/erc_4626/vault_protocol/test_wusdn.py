"""Test Spectra USDN Wrapper vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.spectra.wusdn_vault import SpectraUSDNWrapperVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum mainnet at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=21_757_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_spectra_usdn_wrapper_vault(web3: Web3):
    """Read Spectra USDN Wrapper vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x06a491e3efee37eb191d0434f54be6e42509f9d3",
    )
    assert vault.features == {ERC4626Feature.spectra_usdn_wrapper_like}
    assert isinstance(vault, SpectraUSDNWrapperVault)
    assert vault.get_protocol_name() == "Spectra"
    assert vault.name == "USDN Wrapper"

    # Spectra USDN wrapper has no fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://app.spectra.finance"
