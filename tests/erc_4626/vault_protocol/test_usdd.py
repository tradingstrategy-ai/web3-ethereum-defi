"""Test Decentralized USD (USDD) vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.usdd.vault import USSDVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_usdd_susdd_ethereum(
    web3: Web3,
    tmp_path: Path,
):
    """Read USDD sUSDD vault metadata on Ethereum."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xC5d6A7B61d18AfA11435a889557b068BB9f29930",
    )

    assert isinstance(vault, USSDVault)
    assert vault.get_protocol_name() == "Decentralized USD"
    assert vault.features == {ERC4626Feature.usdd_like}

    # USDD does not charge fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://usdd.io/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # USDD doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_usdd_savings_usdd_ethereum(
    web3: Web3,
    tmp_path: Path,
):
    """Read USDD SavingsUsdd vault metadata on Ethereum."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf94f97677914d298844ec8fa590fab09ccc324d0",
    )

    assert isinstance(vault, USSDVault)
    assert vault.get_protocol_name() == "Decentralized USD"
    assert vault.features == {ERC4626Feature.usdd_like}

    # USDD does not charge fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://usdd.io/"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # USDD doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False
