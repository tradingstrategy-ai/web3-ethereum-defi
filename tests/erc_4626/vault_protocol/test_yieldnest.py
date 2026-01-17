"""Test YieldNest vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import get_vault_protocol_name, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YieldNestVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skip(reason="YieldNest proxy contract is screwed up and detection cannot be run")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility

    Contract created at block 22,674,309 in June 2024
    Latest block as of 2026-01-15: 24,239,327
    """
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_239_327)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_yieldnest(
    web3: Web3,
    tmp_path: Path,
):
    """Read YieldNest vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8",
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
