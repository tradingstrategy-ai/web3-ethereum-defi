"""Test vault blacklist detection on Avalanche.

Tests that vaults flagged due to xUSD exposure are correctly detected as blacklisted.
"""

import os
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import AVALANCHE_MIDNIGHT_BLOCK

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run these tests"),
    # Shared with the other Avalanche midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:avalanche:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Avalanche fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_AVALANCHE, AVALANCHE_MIDNIGHT_BLOCK)


def test_varlamore_blacklisted(
    web3: Web3,
    tmp_path: Path,
):
    """Test that Varlamore vgUSDT vault on Avalanche is detected and blacklisted.

    This vault is managed by Varlamore Capital and uses Euler Earn infrastructure.
    It is blacklisted due to xUSD exposure from the Stream Finance incident.

    See:
    - https://x.com/VarlamoreCap/status/1986290754688541003
    - https://snowtrace.io/address/0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x6c09bfdc1df45d6c4ff78dc9f1c13af29eb335d4",
    )

    # Vault is detected as Euler Earn vault (uses Euler Earn infrastructure)
    assert isinstance(vault, EulerEarnVault)
    assert ERC4626Feature.euler_earn_like in vault.features
    assert vault.get_protocol_name() == "Euler"

    # Verify vault is blacklisted due to xUSD exposure
    assert vault.get_risk() == VaultTechnicalRisk.blacklisted

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Euler Earn doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
