"""Test Hyperdrive vault metadata on HyperEVM"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.hyperdrive_hl.vault import HyperdriveVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import HYPERLIQUID_MIDNIGHT_BLOCK

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests"),
    # Shared with the other Hyperliquid midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:hyperliquid:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Hyperliquid fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_HYPERLIQUID, HYPERLIQUID_MIDNIGHT_BLOCK)


@flaky.flaky
def test_hyperdrive_hl(
    web3: Web3,
    tmp_path: Path,
):
    """Read Hyperdrive vault metadata on HyperEVM.

    Hyperdrive HYPE Liquidator (HD-LIQ-HYPE):
    https://purrsec.com/address/0x9271A5C684330B2a6775e96B3C140FC1dC3C89be
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x9271A5C684330B2a6775e96B3C140FC1dC3C89be",
    )

    assert isinstance(vault, HyperdriveVault)
    assert vault.get_protocol_name() == "Hyperdrive"
    assert vault.features == {ERC4626Feature.hyperdrive_hl_like}

    # Fee data - unknown for unverified contracts
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Risk level - dangerous due to unverified contracts and past exploit
    assert vault.get_risk() == VaultTechnicalRisk.dangerous

    # Link to the vault
    assert vault.get_link() == "https://app.hyperdrive.fi/earn"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Hyperdrive doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
