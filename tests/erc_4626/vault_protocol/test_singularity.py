"""Test Singularity Finance vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.singularity.vault import SingularityVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def anvil_base_fork(request) -> AnvilLaunch:
    """Fork Base at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=40_714_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_base_fork):
    web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_singularity(
    web3: Web3,
    tmp_path: Path,
):
    """Read Singularity Finance vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805",
    )

    assert isinstance(vault, SingularityVault)
    assert vault.get_protocol_name() == "Singularity Finance"
    assert ERC4626Feature.singularity_like in vault.features

    # No lock-up period
    assert vault.get_estimated_lock_up() is None

    # Check vault link
    link = vault.get_link()
    assert "singularityfinance.ai" in link


@flaky.flaky
def test_singularity_fees(
    web3: Web3,
):
    """Read Singularity Finance vault fees via manager contract.

    Fees are read from vault.manager().getFees() which returns:
    - managementFee: uint16 in basis points (200 = 2%)
    - performanceFee: uint16 in basis points (1000 = 10%)
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805",
    )

    assert isinstance(vault, SingularityVault)

    # Singularity has custom fee getters via manager contract
    assert vault.has_custom_fees() is True

    # Check manager contract is accessible
    manager = vault.manager_contract
    assert manager is not None

    # Read fees
    management_fee = vault.get_management_fee("latest")
    performance_fee = vault.get_performance_fee("latest")

    assert management_fee is not None
    assert performance_fee is not None

    # Expected fees for this vault at block 40_714_000:
    # Management fee: 200 basis points = 2%
    # Performance fee: 1000 basis points = 10%
    assert pytest.approx(management_fee, rel=0.01) == 0.02
    assert pytest.approx(performance_fee, rel=0.01) == 0.10
