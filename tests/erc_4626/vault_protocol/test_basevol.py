"""Test BaseVol vault metadata."""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.basevol.vault import BaseVolVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(
    JSON_RPC_BASE is None,
    reason="JSON_RPC_BASE needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_base_fork(request) -> AnvilLaunch:
    """Fork Base at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=41_739_118)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_base_fork):
    web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_basevol(
    web3: Web3,
    tmp_path: Path,
):
    """Read BaseVol Genesis Vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf1BE2622fd0f34d520Ab31019A4ad054a2c4B1e0",
    )

    assert isinstance(vault, BaseVolVault)
    assert vault.get_protocol_name() == "BaseVol"
    assert vault.features == {ERC4626Feature.basevol_like}
    assert vault.get_risk() == VaultTechnicalRisk.severe
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_link() == "https://basevol.com/"
    assert vault.name == "Genesis Vault"
    assert vault.denomination_token.symbol == "USDC"
