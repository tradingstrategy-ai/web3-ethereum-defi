"""Test Teller protocol vault metadata.

Teller Protocol is a decentralised lending protocol that enables
long-tail lending pools where liquidity providers can deposit assets
and earn yield from borrower interest payments.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.teller.vault import TellerVault

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
def test_teller_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Teller vault metadata.

    USDC-TIBBIR lending pool on Base.
    https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b",
    )

    assert isinstance(vault, TellerVault)
    assert vault.get_protocol_name() == "Teller"
    assert vault.features == {ERC4626Feature.teller_like}

    # Check vault name and symbol
    assert vault.name == "USDC-TIBBIR shares"
    assert vault.symbol == "USDC-TIBBIR"

    # Check denomination token (USDC on Base)
    assert vault.denomination_token.address.lower() == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    assert vault.denomination_token.symbol == "USDC"

    # Check that fees are not defined (protocol-specific)
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check vault link
    assert vault.get_link() == "https://app.teller.org/base/earn"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Teller doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
