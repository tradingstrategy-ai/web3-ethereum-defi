"""Test Resolv vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.resolv.vault import ResolvVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_resolv(
    web3: Web3,
    tmp_path: Path,
):
    """Read Resolv wstUSR vault metadata.

    wstUSR is an ERC-4626 wrapper around rebasing staked USR token.

    https://etherscan.io/address/0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055",
    )

    assert isinstance(vault, ResolvVault)
    assert vault.get_protocol_name() == "Resolv"
    assert vault.features == {ERC4626Feature.resolv_like}

    # wstUSR is the vault share token
    assert vault.name == "Wrapped stUSR"
    assert vault.symbol == "wstUSR"

    # The underlying asset is USR stablecoin
    assert vault.denomination_token.symbol == "USR"

    # No fees on this vault
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Resolv doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
