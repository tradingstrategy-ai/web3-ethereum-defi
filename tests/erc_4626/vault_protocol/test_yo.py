"""Test Yo vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yo.vault import YoVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")


@pytest.fixture(scope="module")
def web3_ethereum(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Ethereum Yo fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 24_303_785, web3_retries=2)


@pytest.fixture(scope="module")
def web3_base(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Base Yo fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, 26_953_285, web3_retries=2)


@flaky.flaky
@pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run this test",
)
@pytest.mark.xdist_group("fork:ethereum:24303785")
def test_yo_vault_ethereum(web3_ethereum: Web3):
    """Read Yo vault metadata on Ethereum."""

    vault = create_vault_instance_autodetect(
        web3_ethereum,
        vault_address="0x0000000f2eb9f69274678c76222b35eec7588a65",
    )

    assert isinstance(vault, YoVault)
    assert vault.get_protocol_name() == "Yo"
    assert vault.features == {ERC4626Feature.yo_like}

    # Yo vault has custom deposit/withdrawal fees
    assert vault.has_custom_fees() is True

    # Management and performance fees are not applicable for Yo
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # Check the vault link
    assert vault.get_link() == "https://www.yo.xyz/"

    # Yo doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@flaky.flaky
@pytest.mark.skipif(
    JSON_RPC_BASE is None,
    reason="JSON_RPC_BASE needed to run this test",
)
@pytest.mark.xdist_group("fork:base:26953285")
def test_yo_vault_base(web3_base: Web3):
    """Read Yo vault metadata on Base.

    Same vault address deployed on Base:
    https://basescan.org/address/0x0000000f2eb9f69274678c76222b35eec7588a65
    """

    vault = create_vault_instance_autodetect(
        web3_base,
        vault_address="0x0000000f2eb9f69274678c76222b35eec7588a65",
    )

    assert isinstance(vault, YoVault)
    assert vault.get_protocol_name() == "Yo"
    assert vault.features == {ERC4626Feature.yo_like}

    # Yo vault has custom deposit/withdrawal fees
    assert vault.has_custom_fees() is True

    # Management and performance fees are not applicable for Yo
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # Check the vault link
    assert vault.get_link() == "https://www.yo.xyz/"

    # Yo doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False
