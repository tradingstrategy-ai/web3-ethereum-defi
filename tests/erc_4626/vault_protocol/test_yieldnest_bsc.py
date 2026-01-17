"""Test YieldNest vault metadata on Binance Smart Chain"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE")

pytestmark = pytest.mark.skip(reason="YieldNest proxy contract is screwed up and detection cannot be run")


@pytest.fixture(scope="module")
def anvil_bsc_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility

    ynBNB MAX vault contract: 0x32C830f5c34122C6afB8aE87ABA541B7900a2C5F
    Latest block as of 2026-01-15: 75,391,218
    """
    launch = fork_network_anvil(JSON_RPC_BINANCE, fork_block_number=75_391_218)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_bsc_fork):
    web3 = create_multi_provider_web3(anvil_bsc_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_yieldnest_bsc(
    web3: Web3,
    tmp_path: Path,
):
    """Read YieldNest vault metadata on BSC

    ynBNB MAX vault on Binance Smart Chain
    https://bscscan.com/address/0x32C830f5c34122C6afB8aE87ABA541B7900a2C5F
    """

    from eth_defi.erc_4626.classification import create_vault_instance_autodetect
    from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YieldNestVault

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x32C830f5c34122C6afB8aE87ABA541B7900a2C5F",
    )

    assert isinstance(vault, YieldNestVault)
    assert vault.get_protocol_name() == "YieldNest"
    assert vault.features == {ERC4626Feature.yieldnest_like}

    # Check withdrawal fee can be read
    withdrawal_fee = vault.get_withdrawal_fee("latest")
    assert withdrawal_fee is not None
    assert isinstance(withdrawal_fee, float)
    assert withdrawal_fee >= 0

    # Check that management and performance fees return None (not documented)
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check risk level
    assert vault.get_risk() is None

    # Check lock-up period
    assert vault.get_estimated_lock_up() is None

    # Check vault link
    link = vault.get_link()
    assert link == "https://www.yieldnest.finance"
