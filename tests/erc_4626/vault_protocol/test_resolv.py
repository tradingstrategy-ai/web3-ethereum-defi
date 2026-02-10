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
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_422_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


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
