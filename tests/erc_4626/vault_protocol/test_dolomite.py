"""Test Dolomite vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.dolomite.vault import DolomiteVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=422_034_959)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_dolomite_usdc(
    web3: Web3,
    tmp_path: Path,
):
    """Read Dolomite dUSDC vault metadata.

    https://arbiscan.io/address/0x444868b6e8079ac2c55eea115250f92c2b2c4d14
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x444868b6e8079ac2c55eea115250f92c2b2c4d14",
    )

    assert isinstance(vault, DolomiteVault)
    assert vault.get_protocol_name() == "Dolomite"
    assert vault.features == {ERC4626Feature.dolomite_like}

    assert vault.name == "Dolomite: USDC"
    assert vault.symbol == "dUSDC"
    assert vault.denomination_token.symbol == "USDC"

    # Dolomite has no explicit fees at vault level
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() is None


@flaky.flaky
def test_dolomite_usdt(
    web3: Web3,
    tmp_path: Path,
):
    """Read Dolomite dUSDT vault metadata.

    https://arbiscan.io/address/0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8",
    )

    assert isinstance(vault, DolomiteVault)
    assert vault.get_protocol_name() == "Dolomite"
    assert vault.features == {ERC4626Feature.dolomite_like}

    assert vault.name == "Dolomite: USDT"
    assert vault.symbol == "dUSDT"
    # USDT on Arbitrum has a special symbol with Unicode character
    assert "USD" in vault.denomination_token.symbol

    # Dolomite has no explicit fees at vault level
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() is None
