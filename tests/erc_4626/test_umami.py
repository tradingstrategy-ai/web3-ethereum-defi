"""Scan Euler vault metadata"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.euler.vault import EulerVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.umami.vault import UmamiVault

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


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
def test_umami(
    web3: Web3,
    tmp_path: Path,
):
    """Read Euler vault metadata offchain"""

    gmusdc = create_vault_instance_autodetect(
        web3,
        vault_address="0x5f851f67d24419982ecd7b7765defd64fbb50a97",
    )

    assert isinstance(gmusdc, UmamiVault)
    aggregate_vault_contract = gmusdc.fetch_aggregate_vault()
    assert aggregate_vault_contract.address == "0x1E914730B4Cd343aE14530F0BBF6b350d83B833d"

    import ipdb ; ipdb.set_trace()