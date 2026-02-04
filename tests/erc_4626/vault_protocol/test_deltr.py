"""Test Deltr vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.deltr.vault import DeltrVault
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=21_900_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_deltr(
    web3: Web3,
    tmp_path: Path,
):
    """Read Deltr vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xa7a31e6a81300120b7c4488ec3126bc1ad11f320",
    )

    assert isinstance(vault, DeltrVault)
    assert vault.get_protocol_name() == "Deltr"
    assert vault.features == {ERC4626Feature.deltr_like}
    assert vault.get_risk() == VaultTechnicalRisk.dangerous

    # Deltr doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    # so we cannot use address(0) checks for this vault
    assert vault.can_check_max_deposit_and_redeem() is False
