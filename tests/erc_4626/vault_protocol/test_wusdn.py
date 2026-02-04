"""Test Spectra vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.spectra.erc4626_wrapper_vault import SpectraERC4626WrapperVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum mainnet at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=21_757_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_ethereum(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@pytest.fixture(scope="module")
def anvil_monad_fork(request) -> AnvilLaunch:
    """Fork Monad at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_MONAD, fork_block_number=47100000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_monad(anvil_monad_fork):
    web3 = create_multi_provider_web3(anvil_monad_fork.json_rpc_url)
    return web3


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
def test_spectra_usdn_wrapper_vault(web3_ethereum: Web3):
    """Read Spectra USDN Wrapper vault metadata on Ethereum."""

    vault = create_vault_instance_autodetect(
        web3_ethereum,
        vault_address="0x06a491e3efee37eb191d0434f54be6e42509f9d3",
    )
    assert vault.features == {ERC4626Feature.spectra_usdn_wrapper_like}
    assert isinstance(vault, SpectraERC4626WrapperVault)
    assert vault.get_protocol_name() == "Spectra"
    assert vault.name == "USDN Wrapper"

    # Spectra USDN wrapper has no fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://app.spectra.finance"

    # Spectra doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_MONAD is None, reason="JSON_RPC_MONAD needed to run this test")
def test_spectra_erc4626_wrapper_vault_monad(web3_monad: Web3):
    """Read Spectra ERC4626 Wrapper vault metadata on Monad."""

    # sw-earn on Monad
    # https://monadscan.com/address/0x28e60b466a075cecef930d29f7f1b0facf48f950
    vault = create_vault_instance_autodetect(
        web3_monad,
        vault_address="0x28e60b466a075cecef930d29f7f1b0facf48f950",
    )
    assert vault.features == {ERC4626Feature.spectra_erc4626_wrapper_like}
    assert isinstance(vault, SpectraERC4626WrapperVault)
    assert vault.get_protocol_name() == "Spectra"

    # Spectra ERC4626 wrapper has no fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check link
    assert vault.get_link() == "https://app.spectra.finance"

    # Spectra doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
