"""Test Hyperdrive vault metadata on HyperEVM"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.hyperdrive_hl.vault import HyperdriveVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests")


@pytest.fixture(scope="module")
def anvil_hyperliquid_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_HYPERLIQUID, fork_block_number=26_384_447)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_hyperliquid_fork):
    web3 = create_multi_provider_web3(anvil_hyperliquid_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_hyperdrive_hl(
    web3: Web3,
    tmp_path: Path,
):
    """Read Hyperdrive vault metadata on HyperEVM.

    Hyperdrive HYPE Liquidator (HD-LIQ-HYPE):
    https://purrsec.com/address/0x9271A5C684330B2a6775e96B3C140FC1dC3C89be
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x9271A5C684330B2a6775e96B3C140FC1dC3C89be",
    )

    assert isinstance(vault, HyperdriveVault)
    assert vault.get_protocol_name() == "Hyperdrive"
    assert vault.features == {ERC4626Feature.hyperdrive_hl_like}

    # Fee data - unknown for unverified contracts
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Risk level - dangerous due to unverified contracts and past exploit
    assert vault.get_risk() == VaultTechnicalRisk.dangerous

    # Link to the vault
    assert vault.get_link() == "https://app.hyperdrive.fi/earn"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Hyperdrive doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
