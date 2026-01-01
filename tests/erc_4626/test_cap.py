"""Test Covered Agent Protocol (CAP) vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.cap.vault import CAPVault
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests"
)


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum mainnet at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_139_050)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_cap_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read CAP vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3ed6aa32c930253fc990de58ff882b9186cd0072",
    )
    assert vault.features == {ERC4626Feature.cap_like}
    assert isinstance(vault, CAPVault)
    assert vault.get_protocol_name() == "CAP"

    # CAP vaults have fees internalised
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

