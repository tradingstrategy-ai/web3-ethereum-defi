"""Test Gearbox Protocol vault metadata."""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.gearbox.vault import GearboxVault, GearboxVaultHistoricalReader
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")


@pytest.fixture(scope="module")
def anvil_plasma_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_PLASMA, fork_block_number=10_696_914)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_plasma(anvil_plasma_fork):
    web3 = create_multi_provider_web3(anvil_plasma_fork.json_rpc_url)
    return web3


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum mainnet at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_326_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3_ethereum(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run this test")
def test_gearbox_hyperithm_usdt0(
    web3_plasma: Web3,
    tmp_path: Path,
):
    """Read Gearbox Hyperithm USDT0 vault metadata on Plasma."""

    vault = create_vault_instance_autodetect(
        web3_plasma,
        vault_address="0xb74760fd26400030620027dd29d19d74d514700e",
    )

    assert isinstance(vault, GearboxVault)
    assert vault.get_protocol_name() == "Gearbox"
    assert vault.features == {ERC4626Feature.gearbox_like}

    # Gearbox has zero fees for passive lenders (internalised in share price)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.gearbox.fi/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.low

    # Gearbox's maxRedeem(address(0)) always returns 0 because it uses
    # min(balanceOf(owner), convertToShares(availableLiquidity())) and address(0) has no balance.
    # This makes address(0) checks unsuitable for global redemption availability.
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0  # Deposits are open (returns large value)
    assert max_redeem == 0  # Always 0 for address(0) due to balance check
    assert vault.can_check_redeem() is False

    # Test lending protocol identification
    assert is_lending_protocol(vault.features) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, GearboxVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "availableLiquidity" in call_names
    assert "totalBorrowed" in call_names


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
def test_gearbox_poolv3_gho(
    web3_ethereum: Web3,
    tmp_path: Path,
):
    """Read Gearbox PoolV3 GHO vault metadata on Ethereum mainnet.

    - https://etherscan.io/address/0x4d56c9cba373ad39df69eb18f076b7348000ae09
    """

    vault = create_vault_instance_autodetect(
        web3_ethereum,
        vault_address="0x4d56c9cba373ad39df69eb18f076b7348000ae09",
    )

    assert isinstance(vault, GearboxVault)
    assert vault.get_protocol_name() == "Gearbox"
    assert vault.features == {ERC4626Feature.gearbox_like}

    # Gearbox has zero fees for passive lenders (internalised in share price)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.gearbox.fi/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.low

    # Gearbox's maxRedeem(address(0)) always returns 0 because it uses
    # min(balanceOf(owner), convertToShares(availableLiquidity())) and address(0) has no balance.
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0  # Deposits are open
    assert max_redeem == 0  # Always 0 for address(0)
    assert vault.can_check_redeem() is False

    # Test lending protocol identification
    assert is_lending_protocol(vault.features) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, GearboxVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "availableLiquidity" in call_names
    assert "totalBorrowed" in call_names
