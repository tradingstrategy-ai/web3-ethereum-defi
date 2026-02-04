"""Test Curvance vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.curvance.vault import CurvanceVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")

pytestmark = pytest.mark.skipif(
    JSON_RPC_MONAD is None,
    reason="JSON_RPC_MONAD needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_monad_fork(request) -> AnvilLaunch:
    """Fork Monad at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_MONAD, fork_block_number=47146721)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_monad_fork):
    web3 = create_multi_provider_web3(anvil_monad_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_curvance_borrowable_ctoken(web3: Web3):
    """Read Curvance BorrowableCToken vault metadata on Monad."""

    # BorrowableCToken for AUSD on Monad
    # https://monadscan.com/address/0xad4aa2a713fb86fbb6b60de2af9e32a11db6abf2
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xad4aa2a713fb86fbb6b60de2af9e32a11db6abf2",
    )

    assert isinstance(vault, CurvanceVault)
    assert vault.get_protocol_name() == "Curvance"

    # Check feature flags
    assert ERC4626Feature.curvance_like in vault.features

    # Check fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None

    # Check lock-up
    assert vault.get_estimated_lock_up() is None

    # Check link
    assert "curvance.com" in vault.get_link()

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Curvance doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
