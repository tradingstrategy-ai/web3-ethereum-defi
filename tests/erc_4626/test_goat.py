"""Goat protocol tests"""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.goat.vault import GoatVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.untangle.vault import UntangleVault
from eth_defi.vault.base import VaultTechnicalRisk

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


@flaky.flaky
def test_goat_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Bwaaa"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x8a1eF3066553275829d1c0F64EE8D5871D5ce9d3",
    )

    assert vault.features == {ERC4626Feature.goat_like}
    assert isinstance(vault, GoatVault)
    assert vault.get_protocol_name() == "Goat Protocol"
    assert vault.name == "Yield Chasing Silo USDC"
    assert vault.denomination_token.address == "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
    assert vault.denomination_token.symbol == "USDC.e"

    profit, loss = vault.fetch_pnl()
    assert profit == Decimal("5.310608")
    assert loss == 0
