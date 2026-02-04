"""Centrifuge vault protocol tests.

Tests for Centrifuge liquidity pool vault detection and metadata reading.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.centrifuge.vault import CentrifugeVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_147_164)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_centrifuge(
    web3: Web3,
    tmp_path: Path,
):
    """Read Centrifuge LiquidityPool vault metadata.

    https://etherscan.io/address/0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xa702ac7953e6a66d2b10a478eb2f0e2b8c8fd23e",
    )

    assert isinstance(vault, CentrifugeVault)
    assert vault.get_protocol_name() == "Centrifuge"

    # Check feature flags
    assert ERC4626Feature.centrifuge_like in vault.features
    assert ERC4626Feature.erc_7540_like in vault.features

    # Verify pool and tranche IDs are accessible
    pool_id = vault.fetch_pool_id()
    tranche_id = vault.fetch_tranche_id()
    assert pool_id > 0
    assert len(tranche_id) == 16  # bytes16

    # Verify get_link() returns expected format
    link = vault.get_link()
    assert link == f"https://app.centrifuge.io/pool/{pool_id}"

    # Check vault risk is set
    from eth_defi.vault.base import VaultTechnicalRisk

    risk = vault.get_risk()
    assert risk == VaultTechnicalRisk.negligible

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_max_deposit_and_redeem() is False


@flaky.flaky
def test_centrifuge_anemoy_jtrsy(
    web3: Web3,
    tmp_path: Path,
):
    """Read Centrifuge Anemoy Liquid Treasury Fund 1 vault metadata.

    https://etherscan.io/address/0x4880799ee5200fc58da299e965df644fbf46780b
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x4880799ee5200fc58da299e965df644fbf46780b",
    )

    assert ERC4626Feature.centrifuge_like in vault.features, f"Got features: {vault.features}"

    assert isinstance(vault, CentrifugeVault)
    assert vault.get_protocol_name() == "Centrifuge"

    # Verify pool and tranche IDs are accessible
    pool_id = vault.fetch_pool_id()
    assert pool_id > 0

    # Verify get_link() returns expected format
    link = vault.get_link()
    assert link == f"https://app.centrifuge.io/pool/{pool_id}"

    risk = vault.get_risk()
    assert risk == VaultTechnicalRisk.negligible

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_max_deposit_and_redeem() is False
