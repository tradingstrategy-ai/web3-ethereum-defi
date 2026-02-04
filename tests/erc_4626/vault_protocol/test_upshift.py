"""Test Upshift vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24140983)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_upshift(
    web3: Web3,
    tmp_path: Path,
):
    """Read Upshift vault metadata.

    Example vault: https://etherscan.io/address/0x69fc3f84fd837217377d9dae0212068ceb65818e
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x69fc3f84fd837217377d9dae0212068ceb65818e",
    )

    assert isinstance(vault, UpshiftVault)
    assert vault.get_protocol_name() == "Upshift"
    assert vault.features == {ERC4626Feature.upshift_like}

    # Vault name should contain "Upshift"
    assert "Upshift" in vault.name
    assert vault.name == "Upshift AZT"
    assert vault.symbol == "upAZT"

    # Upshift has custom fees but they are not directly exposed
    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Upshift uses a daily claim processing system
    assert vault.get_estimated_lock_up().days == 1

    # Vault link should point to the Upshift app
    link = vault.get_link()
    assert "app.upshift.finance" in link
    assert "0x69FC3f84FD837217377d9Dae0212068cEB65818e" in link  # Checksummed address

    # Risk level should be None (not yet assessed)
    assert vault.get_risk() is VaultTechnicalRisk.severe

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Upshift doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
