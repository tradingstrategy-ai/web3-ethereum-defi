"""Test Foxify vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.foxify.vault import FoxifyVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.risk import VaultTechnicalRisk

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
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False
    assert vault.get_risk() == VaultTechnicalRisk.dangerous

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Foxify doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
