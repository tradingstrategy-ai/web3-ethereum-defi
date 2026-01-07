"""Test vault blacklist detection on Avalanche.

Tests that vaults flagged due to xUSD exposure are correctly detected as blacklisted.
"""

import os
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

pytestmark = pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run these tests")


@pytest.fixture(scope="module")
def anvil_avalanche_fork(request) -> AnvilLaunch:
    """Fork Avalanche at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_AVALANCHE, fork_block_number=75_011_514)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_avalanche_fork):
    web3 = create_multi_provider_web3(anvil_avalanche_fork.json_rpc_url)
    return web3


def test_varlamore_blacklisted(
    web3: Web3,
    tmp_path: Path,
):
    """Test that Varlamore vgUSDT vault on Avalanche is detected and blacklisted.

    This vault is managed by Varlamore Capital and uses Euler Earn infrastructure.
    It is blacklisted due to xUSD exposure from the Stream Finance incident.

    See:
    - https://x.com/VarlamoreCap/status/1986290754688541003
    - https://snowtrace.io/address/0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4",
    )

    # Vault is detected as Euler Earn vault (uses Euler Earn infrastructure)
    assert isinstance(vault, EulerEarnVault)
    assert ERC4626Feature.euler_earn_like in vault.features
    assert vault.get_protocol_name() == "Euler"

    # Verify vault is blacklisted due to xUSD exposure
    assert vault.get_risk() == VaultTechnicalRisk.blacklisted
