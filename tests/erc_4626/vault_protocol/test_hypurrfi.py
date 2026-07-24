"""Test HypurrFi vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.hypurrfi.vault import HypurrFiVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import HYPERLIQUID_MIDNIGHT_BLOCK

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests"),
    # Shared with the other Hyperliquid midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:hyperliquid:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Hyperliquid fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_HYPERLIQUID, HYPERLIQUID_MIDNIGHT_BLOCK)


@flaky.flaky
def test_hypurrfi(
    web3: Web3,
    tmp_path: Path,
):
    """Read HypurrFi vault metadata.

    https://hyperevmscan.io/address/0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce",
    )

    assert isinstance(vault, HypurrFiVault)
    assert vault.get_protocol_name() == "HypurrFi"
    assert vault.features == {ERC4626Feature.hypurrfi_like}
    assert vault.name == "hyUSDXL (Purr) - 2"
    assert vault.symbol == "hyUSDXL(PURR)-2"

    # Fees are internalised/unknown
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # HypurrFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
