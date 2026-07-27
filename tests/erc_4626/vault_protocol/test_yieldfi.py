"""Test YieldFi vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yieldfi.vault import YieldFiVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Ethereum YieldFi fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 24_181_767)


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:24181767")
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

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:24181767")
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

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:24181767")
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

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def web3_arbitrum(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Arbitrum YieldFi fork and its warmed RPC cache."""
    if JSON_RPC_ARBITRUM is None:
        pytest.skip("JSON_RPC_ARBITRUM needed to run this test")
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, 299_000_000)


@flaky.flaky
@pytest.mark.xdist_group("fork:arbitrum:299000000")
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

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def web3_base(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Base YieldFi fork and its warmed RPC cache."""
    if JSON_RPC_BASE is None:
        pytest.skip("JSON_RPC_BASE needed to run this test")
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, 41_186_545)


@flaky.flaky
@pytest.mark.xdist_group("fork:base:41186545")
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

    # YieldFi doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False
