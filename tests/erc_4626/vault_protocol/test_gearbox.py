"""Test Gearbox Protocol vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gearbox.vault import GearboxVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests")


@pytest.fixture(scope="module")
def anvil_plasma_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_PLASMA, fork_block_number=10_696_914)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_plasma_fork):
    web3 = create_multi_provider_web3(anvil_plasma_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_gearbox_hyperithm_usdt0(
    web3: Web3,
    tmp_path: Path,
):
    """Read Gearbox Hyperithm USDT0 vault metadata on Plasma."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xb74760fd26400030620027dd29d19d74d514700e",
    )

    assert isinstance(vault, GearboxVault)
    assert vault.get_protocol_name() == "Gearbox"
    assert vault.features == {ERC4626Feature.gearbox_like}

    # Gearbox has zero fees for passive lenders (internalised in share price)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.gearbox.fi/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.low
