"""Test Renalta vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.renalta.vault import RenaltaVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests"),
    # Shared with the other Base midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:base:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Base fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


@flaky.flaky
def test_renalta(
    web3: Web3,
    tmp_path: Path,
):
    """Read Renalta vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0ff79b6d6c0fb5faf54bd26db5ce97062a105f81",
    )

    assert isinstance(vault, RenaltaVault)
    assert vault.get_protocol_name() == "Renalta"
    assert vault.features == {ERC4626Feature.renalta_like}

    # Fees are unknown due to unverified contract
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Risk level is dangerous due to unverified source code
    assert vault.get_risk() == VaultTechnicalRisk.dangerous

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Renalta doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
