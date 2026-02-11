"""IPOR Base mainnet fork based tests.

- Read various information out of the vault
"""

import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault, IPORVaultHistoricalReader
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_UTILISATION,
    VaultSpec,
)

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


@pytest.fixture(scope="module")
def test_block_number() -> int:
    return 27975506


@pytest.fixture()
def vault(web3) -> IPORVault:
    """TODO: Optimise test speed - fetch vault data only once per this module"""
    spec = VaultSpec(8545, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216")
    return IPORVault(web3, spec)


def test_ipor_fee(
    web3: Web3,
    vault: IPORVault,
    test_block_number,
):
    """Read IPOR vault fees."""
    block_number = test_block_number
    assert vault.get_management_fee(block_number) == 0.01
    assert vault.get_performance_fee(block_number) == 0.10


# 500 Server Error: Internal Server Error for url:
# dRPC being flaky
@flaky.flaky
@pytest.mark.skipif(CI, reason="Anvil crap on Github")
def test_ipor_identify(
    web3: Web3,
    vault: IPORVault,
    test_block_number,
):
    """Identify IPOR vault."""
    features = detect_vault_features(web3, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216")
    assert features == {ERC4626Feature.ipor_like}


@flaky.flaky
def test_ipor_deposit_redemption_status(
    web3: Web3,
    vault: IPORVault,
):
    """Test deposit/redemption status methods."""
    deposit_reason = vault.fetch_deposit_closed_reason()
    redemption_reason = vault.fetch_redemption_closed_reason()
    deposit_next = vault.fetch_deposit_next_open()
    redemption_next = vault.fetch_redemption_next_open()

    # IPOR utilisation-based - check reasons are either None or start with valid constants
    assert deposit_reason is None or deposit_reason.startswith(DEPOSIT_CLOSED_UTILISATION)
    assert redemption_reason is None

    # IPOR has no timing info (utilisation-based)
    assert deposit_next is None
    assert redemption_next is None

    # IPOR maxRedeem is unreliable, so we skip it
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_ipor_utilisation(
    web3: Web3,
    vault: IPORVault,
):
    """Test IPOR utilisation API."""
    # Test lending protocol identification (vault fixture doesn't have features set,
    # so we test directly with the known feature)
    assert is_lending_protocol({ERC4626Feature.ipor_like}) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, IPORVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "idle_assets" in call_names
