"Autopool vault tests"

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.autopool.vault import AutoPoolVault
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.untangle.vault import UntangleVault
from eth_defi.usdai.vault import StakedUSDaiVault
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
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


def test_autopool(
    web3: Web3,
    tmp_path: Path,
):
    """Read Autopool metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c",
    )

    assert vault.features == {ERC4626Feature.autopool_like}
    assert isinstance(vault, AutoPoolVault)
    assert vault.get_protocol_name() == "AUTO Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting
