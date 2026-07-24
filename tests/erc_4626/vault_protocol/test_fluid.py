"""Fluid protocol tests"""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault, FluidVaultHistoricalReader

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import PLASMA_MIDNIGHT_BLOCK

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests"),
    # Shared with the other Plasma midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:plasma:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Plasma fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_PLASMA, PLASMA_MIDNIGHT_BLOCK)


@flaky.flaky
def test_fluid_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Test Fluid fToken vault metadata on Plasma chain.

    https://plasmascan.to/address/0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B",
    )

    assert vault.features == {ERC4626Feature.fluid_like}
    assert isinstance(vault, FluidVault)
    assert vault.get_protocol_name() == "Fluid"

    # Check that fees are as expected (internalised, no explicit fees)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None

    # Check the link
    assert vault.get_link() == "https://fluid.io/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Fluid doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False

    # Test lending protocol identification
    assert is_lending_protocol({ERC4626Feature.fluid_like}) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, FluidVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "idle_assets" in call_names
