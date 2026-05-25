"""Test ForgeYields vault metadata.

ForgeYields is a cross-chain, non-custodial yield aggregator deploying into
frontier DeFi strategies underwritten by the Hallmark risk methodology.

1. Fork Ethereum at a known block
2. Auto-detect the fyUSDC vault via CONTROLLER_DOMAIN() probe
3. Verify protocol name, features, fees, and vault link
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.forgeyields.vault import ForgeYieldsVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)

#: fyUSDC vault on Ethereum
FYUSDC_ADDRESS = "0x943109DC7C950da4592d85ebd4Cfed007Af64670"


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=25_171_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork) -> Web3:
    """Create Web3 connection to the Anvil fork."""
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_forgeyields(
    web3: Web3,
    tmp_path: Path,
):
    """Read ForgeYields fyUSDC vault metadata.

    1. Auto-detect the vault using CONTROLLER_DOMAIN() probe
    2. Verify it is identified as ForgeYieldsVault
    3. Check protocol name, features, token info
    4. Verify fee data (20% performance, 0% management)
    5. Verify vault link
    """
    # 1. Auto-detect the vault
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=FYUSDC_ADDRESS,
    )

    # 2. Verify vault type
    assert isinstance(vault, ForgeYieldsVault)

    # 3. Check protocol name and features
    assert vault.get_protocol_name() == "ForgeYields"
    assert ERC4626Feature.forgeyields_like in vault.features

    # 4. Verify fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.20)

    # 5. Verify vault link
    assert vault.get_link() == "https://app.forgeyields.com/"
