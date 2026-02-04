"""Test YieldFi vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yieldfi.vault import YieldFiVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24181767)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_yieldfi(
    web3: Web3,
    tmp_path: Path,
):
    """Read YieldFi vyUSD vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x2e3c5e514eef46727de1fe44618027a9b70d92fc",
    )

    assert isinstance(vault, YieldFiVault)
    assert vault.get_protocol_name() == "YieldFi"
    assert ERC4626Feature.yieldfi_like in vault.features

    # Fee data - YieldFi has configurable fees but currently set to 0
    assert vault.get_management_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://yield.fi/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_yieldfi_yusd_ethereum(
    web3: Web3,
):
    """Read YieldFi yUSD vault metadata on Ethereum"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x1ce7d9942ff78c328a4181b9f3826fee6d845a97",
    )

    assert isinstance(vault, YieldFiVault)
    assert vault.get_protocol_name() == "YieldFi"
    assert ERC4626Feature.yieldfi_like in vault.features

    # Fee data - YieldFi has configurable fees but currently set to 0
    assert vault.get_management_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://yield.fi/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_yieldfi_yusd_ethereum_2(
    web3: Web3,
):
    """Read YieldFi yUSD vault metadata on Ethereum (0x19ebd191)"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x19ebd191f7a24ece672ba13a302212b5ef7f35cb",
    )

    assert isinstance(vault, YieldFiVault)
    assert vault.get_protocol_name() == "YieldFi"
    assert ERC4626Feature.yieldfi_like in vault.features

    # Fee data - YieldFi has configurable fees but currently set to 0
    assert vault.get_management_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://yield.fi/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Fork Arbitrum at a specific block for reproducibility"""
    if JSON_RPC_ARBITRUM is None:
        pytest.skip("JSON_RPC_ARBITRUM needed to run this test")
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=299000000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_arbitrum(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_yieldfi_arbitrum(
    web3_arbitrum: Web3,
):
    """Read YieldFi yUSD vault metadata on Arbitrum"""

    vault = create_vault_instance_autodetect(
        web3_arbitrum,
        vault_address="0x4772d2e014f9fc3a820c444e3313968e9a5c8121",
    )

    assert isinstance(vault, YieldFiVault)
    assert vault.get_protocol_name() == "YieldFi"
    assert ERC4626Feature.yieldfi_like in vault.features

    # Fee data - YieldFi has configurable fees but currently set to 0
    assert vault.get_management_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://yield.fi/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def anvil_base_fork(request) -> AnvilLaunch:
    """Fork Base at a specific block for reproducibility"""
    if JSON_RPC_BASE is None:
        pytest.skip("JSON_RPC_BASE needed to run this test")
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=41186545)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_base(anvil_base_fork):
    web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_yieldfi_base(
    web3_base: Web3,
):
    """Read YieldFi vyUSD vault metadata on Base"""

    vault = create_vault_instance_autodetect(
        web3_base,
        vault_address="0xf4f447e6afa04c9d11ef0e2fc0d7f19c24ee55de",
    )

    assert isinstance(vault, YieldFiVault)
    assert vault.get_protocol_name() == "YieldFi"
    assert ERC4626Feature.yieldfi_like in vault.features

    # Fee data - YieldFi has configurable fees but currently set to 0
    assert vault.get_management_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://yield.fi/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
