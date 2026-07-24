"""Test Dolomite vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.dolomite.vault import DolomiteVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Shared with the other Arbitrum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_dolomite_usdc(
    web3: Web3,
    tmp_path: Path,
):
    """Read Dolomite dUSDC vault metadata.

    https://arbiscan.io/address/0x444868b6e8079ac2c55eea115250f92c2b2c4d14
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x444868b6e8079ac2c55eea115250f92c2b2c4d14",
    )

    assert isinstance(vault, DolomiteVault)
    assert vault.get_protocol_name() == "Dolomite"
    assert vault.features == {ERC4626Feature.dolomite_like}

    assert vault.name == "Dolomite: USDC"
    assert vault.symbol == "dUSDC"
    assert vault.denomination_token.symbol == "USDC"

    # Dolomite has no explicit fees at vault level
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() is None

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Dolomite doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_dolomite_usdt(
    web3: Web3,
    tmp_path: Path,
):
    """Read Dolomite dUSDT vault metadata.

    https://arbiscan.io/address/0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8",
    )

    assert isinstance(vault, DolomiteVault)
    assert vault.get_protocol_name() == "Dolomite"
    assert vault.features == {ERC4626Feature.dolomite_like}

    assert vault.name == "Dolomite: USDT"
    assert vault.symbol == "dUSDT"
    # USDT on Arbitrum has a special symbol with Unicode character
    assert "USD" in vault.denomination_token.symbol

    # Dolomite has no explicit fees at vault level
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() is None

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Dolomite doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
