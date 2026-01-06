"""TrueFI protocol tests"""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.goat.vault import GoatVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.truefi.vault import TrueFiVault
from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault
from eth_defi.vault.base import VaultTechnicalRisk

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
def test_truefi_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """TrueFI vault https://app.truefi.io/vault/aloc/42161/0x1fe806928Cf2dd6B917e10d3a8E7B631b4E4940c"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x1fe806928Cf2dd6B917e10d3a8E7B631b4E4940c",
    )

    assert vault.features == {ERC4626Feature.truefi_like}
    assert isinstance(vault, TrueFiVault), f"Got: {type(vault)}: {vault}"
    assert vault.get_protocol_name() == "TrueFi"
    assert vault.name == "Gravity Team LTD"
    assert vault.denomination_token.symbol == "USDC"
