"""Test EulerEarn vault metadata."""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerEarnVaultHistoricalReader
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

pytestmark = pytest.mark.skipif(
    JSON_RPC_AVALANCHE is None,
    reason="JSON_RPC_AVALANCHE needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_avalanche_fork(request) -> AnvilLaunch:
    """Fork Avalanche at a specific block for reproducibility."""
    # Block number when the vault was active (Jan 2025)
    launch = fork_network_anvil(JSON_RPC_AVALANCHE, fork_block_number=75_011_514)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_avalanche_fork):
    web3 = create_multi_provider_web3(anvil_avalanche_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_euler_earn(
    web3: Web3,
    tmp_path: Path,
):
    """Read EulerEarn vault metadata.

    https://snowtrace.io/address/0xE1A62FDcC6666847d5EA752634E45e134B2F824B
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xE1A62FDcC6666847d5EA752634E45e134B2F824B",
    )

    assert isinstance(vault, EulerEarnVault)
    assert ERC4626Feature.euler_earn_like in vault.features
    assert vault.get_protocol_name() == "Euler"

    # EulerEarn has negligible risk (same as EVK)
    assert vault.get_risk() == VaultTechnicalRisk.blacklisted

    # No management fee, performance fee read from chain
    assert vault.get_management_fee("latest") == 0.0
    performance_fee = vault.get_performance_fee("latest")
    assert performance_fee is not None
    assert 0.0 <= performance_fee <= 0.5  # Max 50%

    # Check EulerEarn-specific methods
    supply_queue_length = vault.get_supply_queue_length()
    assert supply_queue_length is not None
    assert supply_queue_length >= 0

    withdraw_queue_length = vault.get_withdraw_queue_length()
    assert withdraw_queue_length is not None
    assert withdraw_queue_length >= 0

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0
    assert max_redeem == 0

    # EulerEarn doesn't support address(0) checks for maxDeposit/maxRedeem
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
    assert isinstance(reader, EulerEarnVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "idle_assets" in call_names or "fee" in call_names
