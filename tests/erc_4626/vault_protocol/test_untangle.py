"""Untangle Finance vault tests"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Shared with the other Arbitrum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_untangle(
    web3: Web3,
    tmp_path: Path,
):
    """Read Untangle vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9",
    )

    assert isinstance(vault, UntangleVault)
    assert vault.features == {ERC4626Feature.erc_7540_like, ERC4626Feature.untangled_like}
    assert vault.get_protocol_name() == "Untangle Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.has_custom_fees() is False

    modules = vault.fetch_modules()
    assert modules.withdrawModule == "0x85501D012d38c28bB08BD4297F9e1f9Ff48b636a"
    assert modules.valuationModule == "0xc34C4ea200F6dE3Ddc07628acA9Af8347384A616"
    assert modules.authModule == "0x0000000000000000000000000000000000000000"

    # Check maxDeposit/maxRedeem with address(0)
    # Untangle returns large values (no per-address cap)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0
    assert max_redeem == 0

    # Untangle doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
