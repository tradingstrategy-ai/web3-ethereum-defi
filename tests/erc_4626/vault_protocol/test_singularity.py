"""Test Singularity Finance vault metadata"""

import logging
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.singularity.vault import SingularityVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

# pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


pytestmark = pytest.mark.skip(reason="Something in tests caused Anvil to timeout always")


@pytest.fixture()
def anvil_base_fork(request) -> AnvilLaunch:
    """Fork Base at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=40_845_127)
    try:
        yield launch
    finally:
        launch.close(log_level=logging.INFO)


@pytest.fixture()
def web3(anvil_base_fork):
    # TODO: Something in tests causes abnormal read timeout.
    # Anvil syncs tons of state?
    web3 = create_multi_provider_web3(
        anvil_base_fork.json_rpc_url,
        default_http_timeout=(6, 60),
    )
    return web3


@flaky.flaky
def test_singularity(
    web3: Web3,
    tmp_path: Path,
):
    """Read Singularity Finance vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805",
    )

    assert isinstance(vault, SingularityVault)
    assert vault.get_protocol_name() == "Singularity Finance"
    assert ERC4626Feature.singularity_like in vault.features

    # Fees are internalised, no explicit fee getters
    assert vault.has_custom_fees() is False
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # No lock-up period
    assert vault.get_estimated_lock_up() is None

    # Check vault link
    link = vault.get_link()
    assert "singularityfinance.ai" in link

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Singularity doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
