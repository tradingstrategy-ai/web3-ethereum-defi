"""Morpho Vault V2 protocol tests.

Tests for the newer Morpho Vault V2 adapter-based architecture.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.morpho_v2.vault import MorphoV2Vault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=420_581_609)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_morpho_v2_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Morpho Vault V2 metadata.

    Steakhouse High Yield Turbo vault on Arbitrum.
    https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xbeefff13dd098de415e07f033dae65205b31a894",
    )

    assert isinstance(vault, MorphoV2Vault)
    assert vault.features == {ERC4626Feature.morpho_v2_like}
    assert vault.get_protocol_name() == "Morpho"
    assert vault.name == "Steakhouse High Yield Turbo"
    assert vault.symbol == "ptUSDCturbo"

    # USDC on Arbitrum
    assert vault.denomination_token.address == "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert vault.denomination_token.symbol == "USDC"

    # Morpho V2 has performance and management fees (both are 0 for this vault)
    management_fee = vault.get_management_fee("latest")
    performance_fee = vault.get_performance_fee("latest")
    assert management_fee == 0.0
    assert performance_fee == 0.0

    # Check adapters count
    adapters_count = vault.get_adapters_count("latest")
    assert adapters_count == 2

    # Check link format
    link = vault.get_link()
    assert "morpho.org" in link
    assert "arbitrum" in link.lower()
