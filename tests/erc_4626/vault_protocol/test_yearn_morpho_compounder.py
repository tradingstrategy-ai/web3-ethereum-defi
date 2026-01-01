"""Test Yearn Morpho Compounder strategy vault metadata."""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder import YearnMorphoCompounderStrategy

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24140000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_yearn_morpho_compounder(
    web3: Web3,
    tmp_path: Path,
):
    """Read Yearn Morpho Compounder strategy vault metadata.

    Example vault: ysUSDT (Morpho Gauntlet USDT)
    https://etherscan.io/address/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F",
    )

    assert isinstance(vault, YearnMorphoCompounderStrategy)
    assert vault.get_protocol_name() == "Yearn Morpho Compounder"

    # Check feature flags
    assert ERC4626Feature.yearn_morpho_compounder_like in vault.features

    # Check fees (Yearn internalises fees into share price)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check vault link
    link = vault.get_link()
    assert "yearn.fi" in link
    assert vault.vault_address.lower() in link.lower()

    # Check no lock-up
    assert vault.get_estimated_lock_up().total_seconds() == 0
