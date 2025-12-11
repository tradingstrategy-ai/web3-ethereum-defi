"""Superform protocol tests"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.superform.vault import SuperformVault


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=409_518_181)
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
def test_superform_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Superform vault https://app.superform.xyz/vault/1_0x0655977feb2f289a4ab78af67bab0d17aab84367"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xa7781f1d982eb9000bc1733e29ff5ba2824cdbe5",
    )

    assert vault.features == {ERC4626Feature.superform_like}
    assert isinstance(vault, SuperformVault), f"Got: {type(vault)}: {vault}"
    assert vault.get_protocol_name() == "Superform"
    assert vault.name == "Yield Chasing crvUSD"
    assert vault.denomination_token.symbol == "crvUSD"
