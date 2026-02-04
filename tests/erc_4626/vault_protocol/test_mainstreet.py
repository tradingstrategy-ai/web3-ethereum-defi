"""Test Mainstreet Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.mainstreet.vault import MainstreetVault

JSON_RPC_SONIC = os.environ.get("JSON_RPC_SONIC")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM or not JSON_RPC_SONIC, reason="JSON_RPC_SONIC and JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_sonic_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_SONIC, fork_block_number=59_684_622)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_sonic_fork):
    web3 = create_multi_provider_web3(anvil_sonic_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_mainstreet_legacy_smsUSD(
    web3: Web3,
    tmp_path: Path,
):
    """Read Mainstreet Finance legacy smsUSD vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc7990369DA608C2F4903715E3bD22f2970536C29",
    )

    assert isinstance(vault, MainstreetVault)
    assert vault.get_protocol_name() == "Mainstreet Finance"
    assert vault.features == {ERC4626Feature.mainstreet_like}

    # Mainstreet has 20% performance fee (10% insurance + 10% treasury)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://mainstreet.finance/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Mainstreet doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run this test")
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_217_821)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_ethereum(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_mainstreet_staked_msusd_ethereum(
    web3_ethereum: Web3,
    tmp_path: Path,
):
    """Read Mainstreet Finance Staked msUSD vault metadata on Ethereum.

    The smart contract is developed by Mainstreet Labs.
    https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a
    """

    vault = create_vault_instance_autodetect(
        web3_ethereum,
        vault_address="0x890a5122aa1da30fec4286de7904ff808f0bd74a",
    )

    assert isinstance(vault, MainstreetVault)
    assert vault.get_protocol_name() == "Mainstreet Finance"
    assert vault.features == {ERC4626Feature.mainstreet_like}

    # Check vault name override
    assert vault.name == "Staked msUSD"

    # Mainstreet has 20% performance fee (10% insurance + 10% treasury)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://mainstreet.finance/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Mainstreet doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
