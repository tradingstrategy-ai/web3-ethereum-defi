"""D2 Finance vault tests"""

import datetime
import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault, Epoch
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Read gmUSDC vault at a specific block"""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=392_313_989)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_d2(
    web3: Web3,
    tmp_path: Path,
):
    """Read D2 vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x75288264FDFEA8ce68e6D852696aB1cE2f3E5004",
    )

    assert isinstance(vault, D2Vault)
    assert vault.get_protocol_name() == "D2 Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    epoch_id = vault.fetch_current_epoch_id()
    assert epoch_id == 12

    epoch = vault.fetch_current_epoch_info()
    assert epoch == Epoch(funding_start=datetime.datetime(2025, 10, 6, 16, 0), epoch_start=datetime.datetime(2025, 10, 7, 16, 0), epoch_end=datetime.datetime(2025, 11, 7, 8, 0))
