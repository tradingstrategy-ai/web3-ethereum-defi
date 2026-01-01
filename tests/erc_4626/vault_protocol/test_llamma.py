"LLAMMA vault tests"

import os
from pathlib import Path

import pytest

from web3 import Web3


from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.llamma.vault import LLAMMAVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
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


def test_llamma(
    web3: Web3,
    tmp_path: Path,
):
    """Read NashPoint metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d",
    )

    assert vault.features == {ERC4626Feature.llamma_like}
    assert isinstance(vault, LLAMMAVault)
    assert vault.name == "Curve LLAMMA IBTC / crvUSD"
    assert vault.get_protocol_name() == "LLAMMA"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_skimming
    assert vault.collateral_token.symbol == "IBTC"
    assert vault.borrowed_token.symbol == "crvUSD"
    assert vault.denomination_token.symbol == "crvUSD"
