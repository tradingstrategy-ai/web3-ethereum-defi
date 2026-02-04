"""Fluid protocol tests"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests")


@pytest.fixture(scope="module")
def anvil_plasma_fork(request) -> AnvilLaunch:
    """Fork Plasma chain at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_PLASMA, fork_block_number=11_664_904)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_plasma_fork):
    web3 = create_multi_provider_web3(anvil_plasma_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_fluid_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Test Fluid fToken vault metadata on Plasma chain.

    https://plasmascan.to/address/0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B",
    )

    assert vault.features == {ERC4626Feature.fluid_like}
    assert isinstance(vault, FluidVault)
    assert vault.get_protocol_name() == "Fluid"

    # Check that fees are as expected (internalised, no explicit fees)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None

    # Check the link
    assert vault.get_link() == "https://fluid.io/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Fluid doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
