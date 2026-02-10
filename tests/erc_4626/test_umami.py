"""Scan Euler vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.umami.vault import UmamiVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
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
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url, default_http_timeout=(3.0, 90.0))
    return web3


@flaky.flaky
def test_umami(
    web3: Web3,
    tmp_path: Path,
):
    """Read Euler vault metadata offchain"""

    # 0xca11bde05977b3631167028862be2a173976ca11
    # 0x76054B318785b588A3164B2A6eA5476F7cBA51e0
    # 0xca11bde05977b3631167028862be2a173976ca11
    gmusdc = create_vault_instance_autodetect(
        web3,
        vault_address="0x5f851f67d24419982ecd7b7765defd64fbb50a97",
    )

    assert isinstance(gmusdc, UmamiVault)
    aggregate_vault_contract = gmusdc.fetch_aggregate_vault()
    # https://arbiscan.io/address/0x1E914730B4Cd343aE14530F0BBF6b350d83B833d
    assert aggregate_vault_contract.address == "0x1E914730B4Cd343aE14530F0BBF6b350d83B833d"

    assert gmusdc.get_management_fee("latest") == 0.02
    assert gmusdc.get_performance_fee("latest") == 0.20
    assert gmusdc.has_custom_fees() is True
    assert gmusdc.get_protocol_name() == "Umami"
