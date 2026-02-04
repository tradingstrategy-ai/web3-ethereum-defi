"""Test Maple Finance vault metadata."""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.maple.vault import SyrupVault
from eth_defi.erc_4626.vault_protocol.maple.aqru_vault import AQRUPoolVault
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_141_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_maple_syrup_usdc(
    web3: Web3,
    tmp_path: Path,
):
    """Read Maple syrupUSDC vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b",
    )

    assert isinstance(vault, SyrupVault)
    assert vault.get_protocol_name() == "Maple"
    assert vault.features == {ERC4626Feature.maple_like}

    # Maple has internalised fees
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.maple.finance/earn"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.negligible

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Maple doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_maple_syrup_usdt(
    web3: Web3,
    tmp_path: Path,
):
    """Read Maple syrupUSDT vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d",
    )

    assert isinstance(vault, SyrupVault)
    assert vault.get_protocol_name() == "Maple"
    assert vault.features == {ERC4626Feature.maple_like}

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.negligible

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Maple doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_maple_aqru_pool(
    web3: Web3,
    tmp_path: Path,
):
    """Read Maple AQRU Pool (Real-World Receivables) vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xe9d33286f0E37f517B1204aA6dA085564414996d",
    )

    assert isinstance(vault, AQRUPoolVault)
    assert vault.get_protocol_name() == "Maple"
    assert vault.features == {ERC4626Feature.maple_aqru_like}

    # AQRU Pool has internalised fees
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # AQRU Pool has 45-day lock-up
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=45)

    # Check vault link
    assert vault.get_link() == "https://aqru.io/real-world-receivables/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.negligible

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Maple doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
